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


def _manifest(base: Path) -> list[str]:
    names = json.loads((base / "epochs.json").read_bytes())
    if not isinstance(names, list) or any(not isinstance(n, str) or not n or Path(n).name != n for n in names):
        raise ValueError("invalid epoch manifest")
    if names != sorted(set(names)):
        raise ValueError("duplicate or reordered epochs")
    return names


def _head(epoch: Path) -> dict | None:
    """The epoch's committed head, or None when it is missing or unreadable."""
    try:
        value = json.loads((epoch / "head.json").read_bytes())
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or set(value) != {"seq", "hash", "bytes"}
            or type(value["seq"]) is not int or type(value["bytes"]) is not int
            or value["bytes"] <= 0 or not isinstance(value["hash"], str)):
        return None
    return value


def _size(epoch: Path) -> int | None:
    try:
        return (epoch / "entries.jsonl").stat().st_size
    except FileNotFoundError:
        return None


def _row_ending_at(path: Path, end: int) -> dict | None:
    """The one row whose LF is byte `end`, read from the end: O(1) in history."""
    window = min(end, 4 * MAX_REQUEST)
    try:
        with open(path, "rb") as stream:
            stream.seek(end - window)
            raw = stream.read(window)
        if len(raw) != window or not raw.endswith(b"\n"):
            return None
        cut = raw.rfind(b"\n", 0, len(raw) - 1)
        if cut < 0 and window < end:
            return None
        line = raw[cut + 1:]
        row = json.loads(line)
        if not isinstance(row, dict) or canonical(row) + b"\n" != line:
            return None
        return row
    except (OSError, ValueError):
        return None


def checkpoint(base: Path, names: list[str] | None = None) -> dict:
    """Every listed epoch's committed head (None where unreadable), in order."""
    names = _manifest(base) if names is None else names
    return {name: _head(base / name) for name in names}


DOORS = ("send", "publish", "forge")
SAY = "BRNRD_SAY"
# One note per window: a burst of unsaid passages is one note to the person.
NOTE_WINDOW = 900


def _door_problem(event: object) -> str | None:
    """A door passage: which door, what act, and the sentence or its absence. Never judged."""
    if not isinstance(event, dict):
        return "event is not an object"
    ident = event.get("id")
    if not isinstance(ident, str) or not ident:
        return "invalid event id"
    if event.get("door") not in DOORS:
        return "unknown door"
    if not isinstance(event.get("act"), str) or not event["act"]:
        return "invalid door act"
    if not isinstance(event.get("at_ns"), int):
        return "invalid door time"
    if event.get("sentence") is not None and not isinstance(event["sentence"], str):
        return "invalid door sentence"
    return _mark_problem(event.get("mark"))


def _event_problem(event: object) -> str | None:
    if not isinstance(event, dict):
        return "event is not an object"
    ident = event.get("id")
    if not isinstance(ident, str) or not ident:
        return "invalid event id"
    for name in ("input_digest", "output_digest", "source_digest"):
        value = event.get(name)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            return "invalid payload digest"
    return _mark_problem(event.get("mark"))


def _mark_problem(mark: object) -> str | None:
    """A mark (mark.py) is the body's own words about the call, never the call's arguments."""
    if mark is None:
        return None
    if (not isinstance(mark, dict) or set(mark) != {"reach", "what", "for", "back", "inferred", "crossed"}
            or mark["reach"] not in ("look", "change", "final")
            or not all(isinstance(mark[k], str) for k in ("what", "for", "back"))
            or not isinstance(mark["inferred"], list) or not all(isinstance(k, str) for k in mark["inferred"])
            or not isinstance(mark["crossed"], bool)):
        return "invalid mark"
    return None


def _first_row(path: Path) -> dict | None:
    try:
        with open(path, "rb") as stream:
            line = stream.readline(4 * MAX_REQUEST)
        row = json.loads(line)
        return row if isinstance(row, dict) else None
    except (OSError, ValueError):
        return None


def _walk(path: Path, epoch: str, install: str, previous: str, limit: int, seen: set) -> tuple[int, str, int]:
    """Check the committed bytes of one epoch; (entries, final hash, tool events)."""
    seq, head, tools = 0, ZERO, 0
    for row in _rows(path, limit):
        given = dict(row)
        claimed = given.pop("hash")
        if claimed != entry_hash(given) or given["prev"] != head or given["seq"] != seq:
            raise ValueError(f"broken chain at entry {seq}")
        if given["epoch"] != epoch or given["install"] != install:
            raise ValueError("wrong installation/epoch")
        if seq == 0:
            if given["kind"] != "epoch" or given["previous_epoch"] != previous:
                raise ValueError("broken epoch link")
            if "found" in given and not isinstance(given["found"], dict):
                raise ValueError("invalid finding")
        else:
            if given["kind"] not in ("tool", "door"):
                raise ValueError("unknown entry kind")
            problem = (_event_problem if given["kind"] == "tool" else _door_problem)(given["event"])
            if problem:
                raise ValueError(problem)
            if given["event"]["id"] in seen:
                raise ValueError("duplicate event id")
            seen.add(given["event"]["id"])
            tools += 1
        seq, head = seq + 1, claimed
    return seq, head, tools


def verify(base: Path, snapshot: dict | None = None) -> dict:
    """Read-only full walk; the verb's job, never the loom's start.

    An epoch its successor declared unclean (a stop between the journal fsync
    and the head replacement, a missing head, an epoch left off the manifest) is
    reported, its committed bytes checked, its unacknowledged tail counted and
    never repaired; every later epoch is still checked. Anything else wrong is
    collected, the walk goes on, and the call raises with all of it at the end.
    Local rewrites of both files, or loss of the entire root, need a future witness.
    """
    live = snapshot is not None
    names = list(snapshot) if live else _manifest(base)
    if not names:
        raise ValueError("no journal epochs")
    heads = snapshot if live else checkpoint(base, names)
    broken: list[str] = []
    if not live and names != sorted(p.name for p in base.iterdir() if p.is_dir()):
        broken.append("epoch manifest disagrees with the epochs on disk (missing or extra epoch)")
    declared: dict[str, tuple[str, dict]] = {}
    for name in names[1:]:
        first = _first_row(base / name / "entries.jsonl")
        found = first.get("found") if first else None
        if isinstance(found, dict) and isinstance(found.get("epoch"), str):
            declared[found["epoch"]] = (name, found)
    previous, seen, count, unclean = ZERO, set(), 0, []
    for index, name in enumerate(names):
        epoch = base / name
        committed = heads.get(name)
        if committed is not None and (not isinstance(committed, dict) or type(committed.get("bytes")) is not int
                                      or committed["bytes"] <= 0):
            broken.append(f"{name}: invalid committed head")
            committed = None
        size = _size(epoch)
        tail_ok = live and index == len(names) - 1
        if committed is not None:
            try:
                seq, head, tools = _walk(epoch / "entries.jsonl", name, base.name, previous,
                                         committed["bytes"], seen)
                count += tools
                if committed != {"seq": seq - 1, "hash": head, "bytes": committed["bytes"]}:
                    broken.append(f"{name}: committed head disagrees with its entries")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                broken.append(f"{name}: {exc}")
        clean = committed is not None and (tail_ok or size == committed["bytes"])
        if name in declared:
            successor, found = declared[name]
            if found.get("committed") != committed or found.get("size") != size:
                broken.append(f"{name}: changed after {successor} found it unclean")
            elif committed is not None and size is not None and size < committed["bytes"]:
                broken.append(f"{name}: journal shorter than committed bytes")
            else:
                unclean.append({"epoch": name, "found_by": successor,
                                "committed_bytes": committed["bytes"] if committed else 0,
                                "size": size or 0,
                                "tail_bytes": (size or 0) - (committed["bytes"] if committed else 0)})
        elif not clean:
            broken.append(f"{name}: journal size disagrees with committed head"
                          + (" (an unclean stop no later epoch has recorded, or tampering)"
                             if index == len(names) - 1 else ""))
        if committed is not None:
            previous = committed["hash"]
    if broken:
        raise ValueError("; ".join(broken))
    return {"entries": count, "epochs": len(names), "hash": previous, "unclean": unclean}


def _inspect(base: Path, name: str, listed: bool) -> dict | None:
    """O(1) in history: None when the epoch ended clean, else what was found."""
    epoch = base / name
    committed, size = _head(epoch), _size(epoch)
    if listed and committed is not None and size == committed["bytes"]:
        row = _row_ending_at(epoch / "entries.jsonl", size)
        if row is not None and row.get("seq") == committed["seq"] and row.get("hash") == committed["hash"]:
            body = dict(row)
            if entry_hash({k: v for k, v in body.items() if k != "hash"}) == committed["hash"]:
                return None
    return {"epoch": name, "committed": committed, "size": size, "listed": listed}


class Journal:
    """Called only by the single writer; the lock fences other loom processes.

    Start-up reads the predecessor's committed head and the one row it names,
    never the history: the full walk belongs to `audit verify`. Whatever the
    predecessor left, a new epoch opens; when it did not end clean, the new
    epoch's first entry says what was found. Old bytes are never rewritten.
    """
    def __init__(self, home: Home):
        self.base = home.root / "audit" / home.install_id()
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = open(self.base / "writer.lock", "ab")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest_problem = None
            try:
                manifest = _manifest(self.base) if (self.base / "epochs.json").exists() else []
            except (OSError, ValueError) as exc:
                manifest, manifest_problem = [], str(exc)
            dirs = [p.name for p in self.base.iterdir() if p.is_dir()]
            names = sorted(set(manifest) | set(dirs))
            found = _inspect(self.base, names[-1], names[-1] in manifest) if names else None
            if manifest_problem:
                found = dict(found or {"epoch": names[-1] if names else "", "committed": None,
                                       "size": None, "listed": False}, manifest=manifest_problem)
            if found is not None and not found["epoch"]:
                found = None  # Nothing on disk to speak of; the manifest finding is lost with it.
            previous = next((h["hash"] for h in (_head(self.base / n) for n in reversed(names)) if h), ZERO)
            # New writer lifetime, explicit link to its predecessor.
            self.epoch = self.base / f"{len(names):020d}-{secrets.token_hex(4)}"
            self.epoch.mkdir(mode=0o700)
            _sync_dir(self.base)
            self.stream = open(self.epoch / "entries.jsonl", "ab", buffering=0)
            self.seq, self.head = 0, ZERO
            first = {"kind": "epoch", "previous_epoch": previous}
            if found is not None:
                first["found"] = found
            self.found = found
            self.append(first)
            self.names = names + [self.epoch.name]
            _replace(self.base / "epochs.json", self.names)
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


def _warning(home: Home) -> Path:
    return home.root / "loom" / "audit-warning"


class Writer:
    def __init__(self, home: Home, doors: bool = False):
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
        # The sensors' switch rides with the endpoint, so a door reads one file, never the config.
        _replace(path, {"port": self.socket.getsockname()[1], "token": self.token, "doors": doors})
        path.chmod(0o600)
        # A writer that came up is a new chance: the next outage warns again.
        _warning(home).unlink(missing_ok=True)
        self.thread = threading.Thread(target=self.run, name="audit-writer", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stop.is_set():
                    break
                continue
            with conn:
                try:
                    self.serve(conn)
                except Exception:
                    pass  # One bad, half-sent or unauthenticated request costs that request only.

    def serve(self, conn: socket.socket) -> None:
        """Only a failed journal append sets self.error; nothing a client sends can."""
        conn.settimeout(DEADLINE)
        raw = b""
        while not raw.endswith(b"\n") and len(raw) < MAX_REQUEST:
            part = conn.recv(MAX_REQUEST - len(raw))
            if not part:
                return
            raw += part
        request = json.loads(raw)
        if not isinstance(request, dict) or not isinstance(request.get("token"), str):
            return
        if not secrets.compare_digest(request["token"].encode(), self.token.encode()):
            return
        if request.get("action") == "verify":
            conn.settimeout(5)
            conn.sendall(canonical(checkpoint(self.journal.base, self.journal.names)) + b"\n")
            return
        kind = "door" if "door" in request else "tool"
        event = request.get(kind if kind == "door" else "event")
        if self.error or (_door_problem if kind == "door" else _event_problem)(event):
            return
        value = {"kind": kind, "event": event}
        canonical(value)  # A value the journal cannot encode is the client's fault, not the journal's.
        # No retries: an ack lost after fsync leaves one captured event.
        try:
            self.journal.append(value)
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__  # Never append past a failed write.
            return
        conn.sendall(b"ok\n")

    def close(self):
        self.stop.set()
        self.thread.join()
        self.socket.close()
        _endpoint(self.home).unlink(missing_ok=True)
        self.journal.close()


class Capture:
    """The loom's hold on capture: the writer, or why it did not start.

    `check` is a loom health step (`health.guard("audit", ...)`): it raises while
    a home that asked for capture has none, so a failed start or a writer stopped
    by a failed append is a `loom.degraded` fact and, past the guard's notice
    delay, a notice to the seat; the next good start's pass clears it."""
    def __init__(self, writer: Writer | None, failure: BaseException | None = None):
        self.writer, self.failure = writer, failure

    def check(self) -> None:
        if self.failure is not None:
            raise self.failure
        if self.writer is not None and self.writer.error:
            raise OSError(f"journal append failed, writer stopped appending: {self.writer.error}")

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()


def start(home: Home, on: bool, doors: bool = False) -> Capture:
    """At loom start: drop a dead writer's endpoint, then start one when the home asks."""
    _endpoint(home).unlink(missing_ok=True)
    if not on:
        return Capture(None)
    try:
        return Capture(Writer(home, doors))
    except Exception as exc:
        _warn(home)
        return Capture(None, exc)


def _warn(home: Home) -> None:
    """Once per outage, persistent across hook subprocesses; never a refusal.

    The marker is cleared at each writer start, so a later outage warns again."""
    try:
        try:
            fd = os.open(_warning(home), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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


def note(room: Path, stdin_text: str, phase: str, mark: dict | None = None) -> None:
    home = None
    try:
        home = home_of_room(room)
        # The endpoint is the switch a hook reads: the loom writes it only when
        # the home asked for capture and removes a dead one at start. Capture off
        # costs one failed open here, no config parse.
        try:
            endpoint_raw = _endpoint(home).read_bytes()
        except FileNotFoundError:
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
        if mark is not None:
            event["mark"] = mark
        endpoint = json.loads(endpoint_raw)
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


def passage(room: Path, door: str, act: str, target: str = "") -> None:
    """A sensor at a door: append the passage and its sentence (``BRNRD_SAY``, or none).

    Nothing is refused or judged here. Sensors off (no endpoint, or one without
    ``doors``) costs one failed open. On, a passage with no sentence tells the
    person once per NOTE_WINDOW, through the loom's notice. Never raises."""
    home = None
    try:
        home = home_of_room(room)
        try:
            endpoint = json.loads(_endpoint(home).read_bytes())
        except FileNotFoundError:
            return
        if not endpoint.get("doors"):
            return
        sentence = os.environ.get(SAY, "").strip() or None
        event = {"id": secrets.token_hex(16), "at_ns": time.time_ns(), "door": door, "act": act,
                 "target": target, "sentence": sentence, "strand": room.name,
                 "thread": os.environ.get("BRNRD_THREAD", ""),
                 "engine": os.environ.get("BRNRD_BODY_SHELL", "unknown")}
        from .mark import parse
        marked = parse(sentence, what=f"{act} {target}".strip())[0]
        if marked is not None:
            event["mark"] = marked.record(True)
        try:
            raw = canonical({"token": endpoint["token"], "door": event}) + b"\n"
            # A door is a whole process already, never a hot path: a wider budget than a hook's.
            with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=1) as conn:
                conn.sendall(raw)
                if conn.recv(3) != b"ok\n":
                    raise OSError("no durable acknowledgement")
        except Exception:
            _warn(home)
        if sentence is None:
            _unsaid(home, event)
    except Exception:
        if home is not None:
            _warn(home)


def _unsaid(home: Home, event: dict) -> None:
    """One note to the person: judgement slipped at this door, with the act."""
    from .ledger import read_facts
    from .loom import _log, notice
    from .verbs_wishes import _person_dm
    facts = read_facts(home)
    to = _person_dm(home, facts, None)
    if to is None:
        _log(home, f"doors: {event['door']} {event['act']} passed unsaid; no person has a direct channel")
        return
    window = event["at_ns"] // 1_000_000_000 // NOTE_WINDOW
    where = f"thread {event['thread']}" if event["thread"] else f"strand {event['strand']}"
    notice(home, facts, "door-unsaid", str(window),
           f"a {event['door']} passage went out with no sentence: {event['act']}"
           + (f" {event['target']}" if event["target"] else "") + f", on {where}",
           f"nothing was stopped. Later unsaid passages in the next {NOTE_WINDOW // 60} min are counted, "
           f"not told: `brnrd runtime audit doors`", channel=to, passive=True)


def doors(base: Path, since_ns: int = 0, until_ns: int | None = None) -> dict:
    """Per door over a period: passages seen and passages declared, from committed bytes only."""
    counts = {door: {"seen": 0, "declared": 0} for door in DOORS}
    for name in _manifest(base):
        head = _head(base / name)
        if not head:
            continue
        for row in _rows(base / name / "entries.jsonl", head["bytes"]):
            event = row.get("event") if row.get("kind") == "door" else None
            if not event or event["at_ns"] < since_ns or (until_ns is not None and event["at_ns"] >= until_ns):
                continue
            counts[event["door"]]["seen"] += 1
            counts[event["door"]]["declared"] += event.get("sentence") is not None
    return counts


def marks(base: Path, since_ns: int = 0, until_ns: int | None = None) -> dict:
    """Marks over a period, from committed bytes: per reach, crossed or not, and calls seen."""
    counts = {"calls": 0, "marked": {r: 0 for r in ("look", "change", "final")}, "crossed": 0, "inferred": 0}
    for name in _manifest(base):
        head = _head(base / name)
        if not head:
            continue
        for row in _rows(base / name / "entries.jsonl", head["bytes"]):
            event = row.get("event") if row.get("kind") == "tool" else None
            if not event or event.get("phase") != "pre":
                continue
            at = event.get("at_ns", 0)
            if at < since_ns or (until_ns is not None and at >= until_ns):
                continue
            counts["calls"] += 1
            mark = event.get("mark")
            if mark:
                counts["marked"][mark["reach"]] += 1
                counts["crossed"] += mark["crossed"]
                counts["inferred"] += bool(mark["inferred"])
    return counts


def _ns(text: str | None) -> int | None:
    if text is None:
        return None
    from datetime import datetime, timezone
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp() * 1_000_000_000)


def main(argv=None) -> int:
    parser = Parser(verb="audit", description="Verify the local record-only tool journal (no witness).")
    parser.add_argument("action", choices=["verify", "doors", "marks", "pass"])
    parser.add_argument("--home")
    parser.add_argument("--room")
    parser.add_argument("--since", help="doors: ISO time, e.g. 2026-11-16T00:00Z")
    parser.add_argument("--until", help="doors: ISO time, exclusive")
    parser.add_argument("--door", choices=DOORS, help="pass: the door (the gh shim's sensor)")
    parser.add_argument("--act", help="pass: what went through")
    parser.add_argument("--target", default="")
    words = list(sys.argv[1:] if argv is None else argv)
    end = words.index("--") if "--" in words else len(words)
    args = parser.parse_args(words[:end])
    forge_argv = words[end + 1:]
    if args.action == "pass":
        if not (args.room and args.door and args.act):
            return refuse("audit", ValueError("pass needs --room, --door and --act"))
        from .mark import door_refusal
        refusal = door_refusal(args.room, command=["gh", *forge_argv] if forge_argv else None)
        if refusal:
            # Exit 3 is the door's own refusal; the gh shim stops on it and on nothing else.
            sys.stderr.write(f"{args.door}: {refusal}\n")
            return 3
        passage(Path(args.room), args.door, args.act, args.target)
        return 0
    home = home_for(args.home, args.room)
    if args.action in ("doors", "marks"):
        try:
            count = doors if args.action == "doors" else marks
            counts = count(home.root / "audit" / home.install_id(), _ns(args.since) or 0, _ns(args.until))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return refuse("audit", exc, prefix=f"audit: {args.action} unreadable: ")
        print(json.dumps(counts, sort_keys=True))
        return 0
    try:
        endpoint_path = _endpoint(home)
        if endpoint_path.exists():
            endpoint = json.loads(endpoint_path.read_bytes())
            with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=30) as conn:
                conn.sendall(canonical({"token": endpoint["token"], "action": "verify"}) + b"\n")
                with conn.makefile("rb") as stream:
                    # Unbounded: one head per loom start, so this outgrows any fixed cap.
                    snapshot = json.loads(stream.readline())
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
