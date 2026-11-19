"""Requests and proofs for a dashboard confirmation of one final call."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from brr.daemon2.facts import Fact

from . import ed25519, gates
from .config import load_config
from .home import mint
from .ledger import append, kind_facts, read_facts

ID = re.compile(r"c-[a-z0-9]{12}")
ASK_LIFE = timedelta(hours=24)
PROOF_LIFE = timedelta(hours=2)


def now():
    return datetime.now(timezone.utc)


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (AttributeError, ValueError, OverflowError):
        return None


def canon(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(install, thread, what, call):
    return hashlib.sha256(canon([install, thread, "final", what, "none", call])).hexdigest()


def signed_bytes(request, proof):
    return canon({"request": request, "pressed_by": proof["pressed_by"], "pressed_at": proof["pressed_at"]})


def key_for(config):
    try:
        hex_key = config.confirm_key or (Path(__file__).parent / "keys" / "confirm-k1.pub").read_text().strip()
        return bytes.fromhex(hex_key)
    except (OSError, ValueError):
        return b""


def local_enabled(home, config):
    if config.confirm_local == "on":
        return True
    relay = any(item.split("=", 1)[-1] == "relay" for item in config.connectors)
    configured = config.relay_state or relay or (home.root / "loom/gates/cloud.json").exists()
    return not configured


def request_valid(request):
    if not isinstance(request, dict) or set(request) != {
        "v", "kind", "id", "install", "thread", "digest", "reach", "what", "back", "for", "asked_at", "ask_exp"
    }:
        return False
    if type(request["v"]) is not int or request["v"] != 1:
        return False
    if any(not isinstance(value, str) for field, value in request.items() if field != "v"):
        return False
    asked, expiry = instant(request["asked_at"]), instant(request["ask_exp"])
    return bool(request["kind"] == "act" and ID.fullmatch(request["id"])
                and re.fullmatch(r"[0-9a-f]{4}", request["install"])
                and re.fullmatch(r"[0-9a-f]{64}", request["digest"])
                and request["thread"] and request["what"] and request["reach"] == "final"
                and request["back"] == "none" and asked and expiry and expiry - asked == ASK_LIFE)


@lru_cache(maxsize=256)
def _verify_signature(key, message, signature):
    return ed25519.verify(key, message, signature)


def verified(home, token, request, moment, *, expired=False):
    """Return the authenticated press time, including expired proofs when reporting expiry."""
    if not request_valid(request) or request["install"] != home.install_id():
        return None
    if not isinstance(token, dict) or token.get("request") != request:
        return None
    proof = token.get("proof")
    if not isinstance(proof, dict):
        return None
    at = instant(proof.get("pressed_at"))
    if at is None or not instant(request["asked_at"]) <= at <= min(moment, instant(request["ask_exp"])):
        return None
    if not expired and (moment >= instant(request["ask_exp"]) or moment - at >= PROOF_LIFE):
        return None
    config = load_config(home.root)
    if proof.get("verifier") == "local":
        return at if set(proof) == {"verifier", "pressed_at"} and local_enabled(home, config) else None
    if proof.get("verifier") != "server-k1" or set(proof) != {"verifier", "pressed_by", "pressed_at", "sig"}:
        return None
    if not isinstance(proof["pressed_by"], str) or not proof["pressed_by"].startswith("person:"):
        return None
    try:
        signature = base64.b64decode(proof["sig"], validate=True)
        return at if _verify_signature(key_for(config), signed_bytes(request, proof), signature) else None
    except (ValueError, TypeError):
        return None


@contextmanager
def locked(home):
    path = home.root / "loom" / ".confirm.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def entry(facts, ident):
    if not ID.fullmatch(ident):
        return None
    return next((fact.data for fact in kind_facts(facts, "confirm.asked")
                 if request_valid(fact.data.get("request")) and fact.data["request"]["id"] == ident), None)


def put(home, kind, ident, data):
    return append(home, Fact(kind="confirm." + kind, by=f"loom:{home.install_id()}",
                             id=f"confirm.{kind}:{ident}", data=data))


def ask(home, thread, what, wish, call, *, why=""):
    moment = now()
    install = home.install_id()
    hashed = digest(install, thread, what, call)
    with locked(home):
        facts = read_facts(home)
        for fact in reversed(kind_facts(facts, "confirm.asked")):
            req = fact.data.get("request")
            if request_valid(req) and req["digest"] == hashed and state(home, facts, req, moment)[0] in ("pending", "given"):
                return req["id"]
        ident = "c-" + mint(12)
        request = {
            "v": 1, "kind": "act", "id": ident, "install": install, "thread": thread,
            "digest": hashed, "reach": "final", "what": what, "back": "none", "for": wish,
            "asked_at": moment.isoformat(), "ask_exp": (moment + ASK_LIFE).isoformat(),
        }
        clause = re.split(r"[,;.!?\n]", why, maxsplit=1)[0].strip()
        put(home, "asked", ident, {"request": request, "call": call, "why": clause})
        return ident


def state(home, facts, request, moment):
    ident = request["id"]
    used = any(f.data.get("id") == ident for f in kind_facts(facts, "confirm.used"))
    for fact in kind_facts(facts, "confirm.given", "confirm.refused"):
        if fact.kind == "confirm.refused":
            if fact.data.get("id") == ident:
                return "refused", None
            continue
        at = verified(home, fact.data.get("token"), request, moment, expired=True)
        if at is not None:
            if used:
                return "used", at
            expired = moment >= instant(request["ask_exp"]) or moment - at >= PROOF_LIFE
            return ("expired" if expired else "given"), at
    if used:
        return "used", None
    return ("expired" if moment >= instant(request["ask_exp"]) else "pending"), None


def cloud_url(home, config):
    directory = gates.state_dir(home.root, config)
    if directory is not None:
        try:
            return json.loads((directory / "gates" / "cloud.json").read_text())["brnrd_url"].rstrip("/")
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            return ""
    return ""


def local_page(home, ident, decision=None):
    with locked(home):
        facts = read_facts(home)
        row = entry(facts, ident)
        if not row or row["request"]["install"] != home.install_id():
            return 404, {"error": "Unknown confirmation"}
        req = row["request"]
        config = load_config(home.root)
        enabled = local_enabled(home, config)
        moment = now()
        status, pressed = state(home, facts, req, moment)
        if decision is not None:
            if decision not in ("approve", "decline"):
                return 400, {"error": "Unknown decision"}
            if not enabled:
                return 403, {"error": "Use the cloud board to answer"}
            if status != "pending":
                return 409, {"error": "This request has expired or already been answered"}
            if decision == "approve":
                pressed = moment
                proof = {"verifier": "local", "pressed_at": pressed.isoformat()}
                put(home, "given", ident, {"token": {"request": req, "proof": proof}})
                status = "given"
            else:
                put(home, "refused", ident, {"id": ident})
                status = "refused"
        url = cloud_url(home, config) if not enabled else ""
        return 200, {**row, "status": status, "pressed_at": pressed.isoformat() if pressed else None,
                     "can_press": enabled and status == "pending", "cloud": f"{url}/confirm/{ident}" if url else ""}


def at_door(room, mark, argv):
    """Claim the door stage of a hook-authorized invocation, or authorize a direct invocation."""
    import os
    import shlex

    from .grants import judge, note
    from .home import home_of_room, strand_of_room
    from .mark import _stages

    home = home_of_room(room)
    call = {"command": shlex.join(argv)}
    if mark.for_.startswith("confirm:"):
        ident = mark.for_.removeprefix("confirm:")
        thread = os.environ.get("BRNRD_THREAD", "")
        strand = strand_of_room(room)
        with locked(home):
            facts = read_facts(home)
            row = entry(facts, ident)
            used = next((f.data for f in kind_facts(facts, "confirm.used") if f.data.get("id") == ident), None)
            if used is not None:
                if (row and used.get("stage") == "hook" and used.get("strand") == strand
                        and used.get("thread") == thread):
                    req = row["request"]
                    original = row["call"]
                    text = original.get("command", "").strip()
                    try:
                        literal = _stages(text) == [(text, False)] and shlex.split(text) == argv
                    except ValueError:
                        literal = False
                    signed = req["digest"] == digest(home.install_id(), thread, mark.what, original)
                    moment = now()
                    _, pressed = state(home, facts, req, moment)
                    active = pressed is not None and moment < min(instant(req["ask_exp"]), pressed + PROOF_LIFE)
                    already = any(f.data.get("id") == ident for f in kind_facts(facts, "confirm.door"))
                    recorded = any(f.data.get("letter") == ident and f.data.get("strand") == strand
                                   for f in kind_facts(facts, "guard.granted"))
                    if literal and signed and active and recorded and not already:
                        put(home, "door", ident, {"id": ident})
                        return None
                return f"Confirmation {ident} has already been used."
    verdict = judge(room, mark.what, mark.for_, call=call)
    try:
        note(room, verdict, door=True)
    except ValueError as exc:
        return str(exc)
    return None if verdict.granted else verdict.line


def ingest(home, event):
    if not isinstance(event, dict) or not request_valid(event.get("request")):
        return
    req = event["request"]
    ident = req["id"]
    with locked(home):
        if event.get("decision") == "given":
            token = {"request": req, "proof": event.get("proof")}
            # Keep the delivered proof verbatim. The granting and wait paths
            # authenticate it against the local request independently.
            if isinstance(token["proof"], dict) and token["proof"].get("verifier") == "server-k1":
                suffix = hashlib.sha256(canon(token)).hexdigest()
                put(home, "given", f"{ident}:{suffix}:{home.install_id()}", {"token": token})
        elif event.get("decision") == "refused":
            put(home, "refused", f"{ident}:{home.install_id()}", {"id": ident})


def _time(home, person, moment):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    path = home.root / "self" / "people" / person / "page.md"
    try:
        value = re.search(r"(?im)^timezone:\s*([\w/+.-]+)\s*$", path.read_text())
        if value:
            return moment.astimezone(ZoneInfo(value[1]))
    except (OSError, ZoneInfoNotFoundError):
        pass
    return moment.astimezone()


def _message(home, config, row, person, url):
    req = row["request"]
    ident = req["id"]
    expiry = _time(home, person, instant(req["ask_exp"]))
    lines = ["brnrd asks first, because this cannot be undone", "", req["what"]]
    if row.get("why"):
        lines.append(f'Why: {row["why"]}')
    lines += [f'Until {expiry:%a %d %b %H:%M %Z}', ""]
    if url:
        lines.append(f'Press here: {url}/confirm/{ident}')
    if config.board_port:
        lines.append(f'On this computer: http://localhost:{config.board_port}/confirm/{ident}')
    else:
        lines.append("The dashboard on this computer is off.")
    return "\n".join(lines)


def _upload(directory, row):
    from brr.gates import cloud

    state = cloud._load_state_from_dir(directory)
    cloud._request(state["brnrd_url"], "POST", "/v1/daemons/confirms", token=state["token"],
                   json=row, timeout=5, retry=False)


class Notifications:
    """Upload off the loom tick, then speak once through its fenced channel."""

    def __init__(self, home):
        self.home = home
        self.upload = None
        self.retry_after = 0
        self.revision = None
        self.check_at = None

    def step(self, router, facts):
        from concurrent.futures import Future
        from threading import Thread
        from time import monotonic

        from .grants import owners
        from .loom import notice
        from .verbs_wishes import _person_dm

        home, config = self.home, router.config
        moment = now()
        requests = kind_facts(facts, "confirm.asked")
        if not requests:
            return
        revision = tuple(len(kind_facts(facts, "confirm." + kind))
                         for kind in ("asked", "given", "refused", "used"))
        upload_ready = self.upload is not None and self.upload[1].done()
        if revision == self.revision and moment < self.check_at and not upload_ready:
            return
        self.revision, self.check_at = revision, moment + timedelta(seconds=15)
        asked = [f.data for f in requests
                 if request_valid(f.data.get("request")) and f.data["request"]["install"] == home.install_id()]
        if not asked:
            return
        if self.upload is not None:
            ident, future = self.upload
            if future.done():
                self.upload = None
                if future.exception() is None:
                    facts.append(put(home, "uploaded", ident, {"id": ident}))
                else:
                    self.retry_after = monotonic() + 15
        directory = gates.state_dir(home.root, config)
        url = cloud_url(home, config) if directory is not None else ""
        uploaded = {f.data.get("id") for f in kind_facts(facts, "confirm.uploaded")}
        channel = None
        for owner in sorted(owners(home.root / "self")):
            person = owner.removeprefix("person:")
            channel = _person_dm(home, facts, person)
            if channel is not None:
                break

        def tell(kind, ident, text):
            if channel is not None:
                notice(home, facts, "confirm-" + kind, ident, text, "", channel=channel, passive=True)

        for row in asked:
            req = row["request"]
            ident = req["id"]
            status, pressed = state(home, facts, req, moment)
            if status == "pending":
                if directory is None or ident in uploaded:
                    if channel is not None:
                        tell("asked", ident, _message(home, config, row, person, url))
                elif self.upload is None and monotonic() >= self.retry_after:
                    future = Future()
                    self.upload = ident, future

                    def run(path, payload, result):
                        try:
                            _upload(path, payload)
                        except Exception as exc:
                            result.set_exception(exc)
                        else:
                            result.set_result(None)

                    Thread(target=run, args=(directory, row, future), daemon=True).start()
            if pressed is not None or status == "used":
                tell("given", ident, f'You confirmed {req["what"]}.')
            if status == "refused":
                tell("refused", ident, f'You declined {req["what"]}.')
            if status == "expired" and pressed is not None and channel is not None:
                at = _time(home, person, pressed)
                tell("expired", ident,
                     f'You confirmed {req["what"]} at {at:%H:%M}, and nothing was done. It has expired.')
