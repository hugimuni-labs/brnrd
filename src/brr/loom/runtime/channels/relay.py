"""One relay poll into the ledger. Event identity survives cursor resets.

``loom.run`` arms it when ``loom/config.toml`` says ``relay = true``
(slice 3): ``poll_forever`` holds the account's relay lock, so the daemon's
cloud gate stops polling while the loom does. The injected client exposes
``pull(cursor)``, ``send(payload)`` and
``download_attachment(event_id, index, destination)``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from brr.daemon2.facts import Fact, union
from brr.gates import cloud

from ..home import Home, atomic_write
from ..labels import is_stranger
from ..ledger import append, read_facts
from ..project import fold, sender_threads


class RelayClient:
    """Use the existing cloud state directory, including its split token file."""

    def __init__(self, state_dir: Path | str):
        self.state_dir = Path(state_dir)

    def _state(self) -> dict:
        state = cloud._load_state_from_dir(self.state_dir)
        if not state.get("brnrd_url") or not state.get("token"):
            raise RuntimeError("relay: cloud state has no URL or token")
        return state

    def pull(self, cursor: int) -> dict:
        state = self._state()
        return cloud._request(
            state["brnrd_url"], "GET", "/v1/daemons/inbox",
            token=state["token"], params={"since": cursor, "wait": cloud._POLL_WAIT_S},
        )

    def send(self, payload: dict) -> dict:
        """An event answer → ``/responses`` (ack: ``{event_id, forwarded}``, no
        message id today); anything else → ``/messages`` (ack carries
        ``message_id``)."""
        state = self._state()
        if payload.get("event_id"):
            body = {"event_id": payload["event_id"],
                    "body_markdown": payload["body_markdown"], "status": "done"}
            return cloud._request(state["brnrd_url"], "POST", "/v1/daemons/responses",
                                  token=state["token"], json=body)
        return cloud._request(
            state["brnrd_url"], "POST", "/v1/daemons/messages",
            token=state["token"], json=payload,
        )

    def download_attachment(self, event_id: str, index: int, dest: Path) -> bool:
        state = self._state()
        return cloud._download_attachment(
            state["brnrd_url"], state["token"], event_id, index, dest,
        )


def read_cursor(home: Home) -> int:
    path = home.root / "loom" / "relay-cursor.json"
    if not path.is_file():
        return 0
    return _cursor(json.loads(path.read_text(encoding="utf-8"))["cursor"])


def _cursor(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"relay: invalid cursor {value!r}")
    return value


def _write_cursor(home: Home, cursor: int) -> None:
    atomic_write(home.root / "loom" / "relay-cursor.json",
                 json.dumps({"cursor": cursor}) + "\n")


def _sender(home: Home, platform: str, user_id: object) -> str:
    # Only the relay's verified origin fields enter this lookup. Display
    # names, usernames, and text have no authority over the sender.
    if user_id not in (None, ""):
        key = f"{platform}:{user_id}"
        matches = []
        for path in sorted((home.root / "self" / "people").glob("*/channels.md")):
            if key in {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}:
                matches.append(path.parent.name)
        if len(matches) > 1:
            raise ValueError(f"relay: ambiguous person channel {key}")
        if matches:
            return f"person:{matches[0]}"
    return f"stranger:{platform}:{'' if user_id is None else user_id}"


def person_dm(home: Home, channel: str) -> str | None:
    """``channel:<platform>/<chat>`` is a known person's direct chat ⇒ that person.

    In a direct chat the chat id is the user id, so the verified mapping in
    ``people/<name>/channels.md`` names it. Anything else (a group, an
    unknown chat) is ``None``: until audience labels reach outbound speech,
    the loom speaks only into a known person's DM, never into a room with
    strangers in it.
    """
    try:
        platform, chat = channel.split(":", 1)[1].split("/", 1)
    except (AttributeError, ValueError):
        return None
    try:
        sender = _sender(home, platform, chat)
    except ValueError:
        return None
    return sender if sender.startswith("person:") else None


def make_effect(client):
    """The relay effect for ``speak.EFFECTS``: answer an event, or speak to the owner.

    Answering (``context['event_id']``) posts ``{event_id, body_markdown}``.
    Otherwise ``{platform, body_markdown}``: the relay resolves the platform's
    owner chat itself, so an unprompted send can't pick an arbitrary chat.
    The returned dict is the receipt; ``message_id`` is kept when the relay
    returns one. ``/messages`` does (``MessageAck``); ``/responses`` doesn't
    yet (``ResponseAck`` is ``{event_id, forwarded}``), so a reply to the
    *first* part of an event answer can't bind until the relay returns it.
    """
    def effect(home, channel, part_key, text, context):
        platform = channel.split(":", 1)[1].split("/", 1)[0]
        payload = {"body_markdown": text}
        # One event takes one response: only the first part answers it; the
        # rest follow as ordinary messages to the same platform.
        first = "#" not in part_key or part_key.endswith("#1")
        if context.get("event_id") and first:
            payload["event_id"] = context["event_id"]
        else:
            payload["platform"] = platform
        response = client.send(payload) or {}
        receipt = {"via": "event" if "event_id" in payload else "platform"}
        for key in ("message_id", "id"):
            if response.get(key) not in (None, ""):
                receipt["message_id"] = response[key]
                break
        return receipt
    return effect


def _person_channel(to: object, chat: object) -> bool:
    # Chat keys are platform-qualified, so Telegram 42 cannot select a
    # WhatsApp thread whose chat happens to be 42.
    return (isinstance(to, str) and to == f"channel:{chat}"
            and to.split(":", 1)[1].split("/", 1)[0] in {"telegram", "slack", "whatsapp"})


def thread_for_message(facts: list[Fact], channel: str, message_id: object) -> str | None:
    """The thread a platform message came from, from ``speech.part`` receipts.

    Only the loom's own receipts count, and only for the same channel, so a
    message id from another chat (or another platform) binds nothing.
    """
    if message_id in (None, ""):
        return None
    accepted = fold(facts).accepted
    threads = sender_threads(accepted)
    letters = {str(f.data.get("id") or f.id): f for f in accepted if f.kind == "letter"}
    for fact in facts:
        if fact.kind != "speech.part" or not fact.by.startswith("loom:"):
            continue
        data = fact.data or {}
        if data.get("channel") != channel:
            continue
        if str((data.get("receipt") or {}).get("message_id")) == str(message_id):
            letter = letters.get(str(data.get("key")))
            if letter is not None and letter.by.startswith("strand:"):
                return threads.get(letter.by.split(":", 1)[1])
    return None


def route_bare(facts: list[Fact], chat: str, reply_thread: str | None = None) -> str:
    """Where a chat message goes: a reply's thread, else whoever spoke here last.

    ``chat`` is ``<platform>/<chat_id>``. A Telegram reply to a message the
    loom sent goes to that message's thread: the person pointed at it. A
    bare message goes to the thread that last spoke in this chat, whatever
    became of its lease: the thread's README carries the conversation, so a
    molted or released thread wakes with it. ``inbox`` only when no thread
    has spoken here yet. Only confirmed speech (a ``sent`` receipt) counts,
    and the speaker is the strand that wrote the letter, never its text.
    """
    if reply_thread:
        return f"thread:{reply_thread}"
    ordered = union(facts)
    state = fold(ordered)
    threads = sender_threads(state.accepted)
    letters = {
        str(f.data.get("id") or f.id): f
        for f in state.accepted if f.kind == "letter"
    }
    last = None
    for fact in ordered:
        if fact.kind != "speech" or fact.data.get("state") != "sent":
            continue
        letter = letters.get(str(fact.data.get("key")))
        if letter is None or not _person_channel(letter.data.get("to"), chat):
            continue
        sender = (letter.by.split(":", 1)[1] if letter.by.startswith("strand:")
                  else str(letter.data.get("from") or ""))
        thread = threads.get(sender)
        if thread is None:
            continue
        last = thread
    return f"thread:{last}" if last else "thread:inbox"


def _blob(home: Home, client, event_id: str, index: int) -> tuple[str, int]:
    directory = home.root / "blobs"
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".relay-", dir=directory)
    os.close(fd)
    tmp = Path(name)
    try:
        if not client.download_attachment(event_id, index, tmp):
            raise RuntimeError(f"relay: attachment download failed: {event_id}#{index}")
        sha = hashlib.sha256()
        size = 0
        with tmp.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                sha.update(chunk)
                size += len(chunk)
            os.fsync(handle.fileno())
        digest = sha.hexdigest()
        dest = directory / digest
        if not dest.exists():
            os.replace(tmp, dest)
            # Persist the rename as well as the file contents.
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return digest, size
    finally:
        tmp.unlink(missing_ok=True)


#: Pulls that may fail on one event's attachments before it lands without them.
GIVE_UP_AFTER = 3


def _failures_path(home: Home) -> Path:
    return home.root / "loom" / "relay-failures.json"


def _read_failures(home: Home) -> dict:
    try:
        data = json.loads(_failures_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _failure(home: Home, event_id: str) -> int:
    """Count one failed pull for ``event_id``; return the count so far."""
    data = _read_failures(home)
    count = int(data.get(event_id, 0)) + 1
    data[event_id] = count
    atomic_write(_failures_path(home), json.dumps(data, sort_keys=True) + "\n")
    return count


def _clear_failure(home: Home, event_id: str) -> None:
    data = _read_failures(home)
    if event_id in data:
        del data[event_id]
        atomic_write(_failures_path(home), json.dumps(data, sort_keys=True) + "\n")


def _attention(home: Home, fact_id: str, why: str) -> None:
    from ..ledger import LedgerConflict
    try:
        append(home, Fact(kind="attention", by=f"loom:{home.install_id()}",
                          id=fact_id, data={"why": why}))
    except LedgerConflict:
        pass


def _letter(home: Home, source: Fact) -> Fact:
    # Reconstruct from the durable source, not from a replay's mutable text
    # or today's person map. A crash after source append cannot lose a letter
    # or route it differently when another thread subsequently speaks.
    data = source.data
    sender = data["from"]
    ident = source.id.replace("source:", "letter:", 1)
    return append(home, Fact(
        kind="letter", by=f"loom:{home.install_id()}", id=ident,
        after=(source.id,), data={
            "id": ident, "from": sender, "to": data["to"], "body": data["text"],
            "cites": source.id,
            "label": {
                "taint": (sender.startswith("stranger:")
                          or is_stranger(sender, home.root / "self")),
                "audience": ["self"],
            },
        },
    ))


def pull_once(home: Home, client, cursor: int) -> int:
    """Append complete events before committing the server cursor, even a lower one."""
    cursor = _cursor(cursor)
    result = client.pull(cursor)
    next_cursor = _cursor(result.get("cursor", cursor))
    facts = read_facts(home)
    sources = {fact.id: fact for fact in facts if fact.kind == "source"}
    letter_ids = {fact.id for fact in facts if fact.kind == "letter"}
    for event in result.get("events", []):
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("relay: event has no event_id")
        source_id = f"source:relay:{event_id}"
        letter_id = f"letter:relay:{event_id}"
        source = sources.get(source_id)
        if source is not None:
            if letter_id not in letter_ids:
                facts.append(_letter(home, source))
                letter_ids.add(letter_id)
            continue
        origin = event.get("reply_to") or {}
        meta = cloud._origin_meta(origin)
        platform = str(meta["cloud_platform"])
        sender = _sender(home, platform, origin.get("user_id"))
        raw_attachments = event.get("attachments")
        names = cloud._attachment_names(raw_attachments)
        if names is None:
            raise ValueError(f"relay: unreadable attachments for {event_id}")
        pointers = (raw_attachments if isinstance(raw_attachments, (list, tuple))
                    else [raw_attachments])
        blobs = []
        blob_ids = []
        missing = []
        downloaded = {}
        for index in range(len(names)):
            try:
                downloaded[index] = _blob(home, client, event_id, index)
            except Exception:  # noqa: BLE001 — counted, then held or given up
                missing.append(index)
        if missing:
            # One permanently missing file must not block every later
            # message. Hold the cursor for GIVE_UP_AFTER pulls, then land the
            # source without those blobs and say so.
            tries = _failure(home, event_id)
            if tries < GIVE_UP_AFTER:
                raise RuntimeError(
                    f"relay: attachment download failed: {event_id}#"
                    f"{','.join(map(str, missing))} "
                    f"(attempt {tries} of {GIVE_UP_AFTER})"
                )
        for index in range(len(names)):
            if index not in downloaded:
                continue
            sha, size = downloaded[index]
            pointer = pointers[index]
            mime = ((pointer.get("mime_type") if isinstance(pointer, dict) else None)
                    or "application/octet-stream")
            ident = f"blob:relay:{event_id}#{index}"
            facts.append(append(home, Fact(
                kind="blob", by=f"loom:{home.install_id()}", id=ident,
                data={"sha": sha, "mime": mime, "size": size, "origin": f"relay:{event_id}#{index}"},
            )))
            blobs.append(sha)
            blob_ids.append(ident)
        data = {
            "origin": f"relay:{event_id}", "platform": platform,
            "chat": meta["cloud_chat_id"], "topic": meta["cloud_topic_id"],
            "from": sender, "text": event.get("body") or "", "blobs": blobs,
        }
        if missing:
            data["blobs_missing"] = missing
        # Preserve the raw reply target for slice 2 to resolve against sent
        # receipts. message_id is the incoming id, never a reply target.
        if platform == "telegram" and origin.get("reply_to_message_id") is not None:
            data["reply_to_message_id"] = origin["reply_to_message_id"]
        reply_thread = thread_for_message(
            facts, f"channel:{platform}/{data['chat']}", data.get("reply_to_message_id"),
        )
        # Source is durable before letter. Save the routing decision as part
        # of that intent: HLC ordering alone cannot reconstruct the original
        # snapshot once facts from another install arrive during recovery.
        data["to"] = route_bare(
            facts, f"{platform}/{data['chat']}", reply_thread,
        )
        source = append(home, Fact(
            kind="source", by=f"loom:{home.install_id()}", id=source_id,
            after=tuple(blob_ids), data=data,
        ))
        facts.append(source)
        sources[source_id] = source
        facts.append(_letter(home, source))
        letter_ids.add(letter_id)
        if missing:
            _attention(
                home, f"attention:blobs-missing:{event_id}",
                f"relay: gave up on attachment(s) {missing} of {event_id} "
                f"after {GIVE_UP_AFTER} pulls; the message landed without them",
            )
        _clear_failure(home, event_id)
    _write_cursor(home, next_cursor)
    return next_cursor


def poll_forever(home: Home, client, lock, stop, *, log=None,
                 backoff_cap: float = 60.0) -> None:
    """Hold the relay and long-poll it until ``stop`` is set.

    ``lock`` is a ``brr.gates.relay_lock.RelayLock``. The loom declares that
    it wants the relay, waits for the daemon to finish its current poll, then
    starts from whichever cursor is further along: its own, or the one the
    daemon handed over in the lock. Every committed cursor goes back into
    the lock, so stopping the loom hands the relay back without a replay.
    """
    say = log or (lambda message: None)
    lock.want()
    waiting = False
    backoff = 1.0
    try:
        while not stop.is_set():
            if not lock.held:
                if not lock.try_acquire():
                    if not waiting:
                        say(f"relay: waiting for {lock.holder()} to finish its poll")
                        waiting = True
                    stop.wait(0.5)
                    continue
                waiting = False
                handed = lock.cursor()
                own = read_cursor(home)
                if handed is not None and handed > own:
                    say(f"relay: cursor {own} -> {handed} (handed over)")
                    _write_cursor(home, handed)
                say(f"relay: polling from {read_cursor(home)}")
            try:
                cursor = pull_once(home, client, read_cursor(home))
                lock.record(cursor)
                backoff = 1.0
            except Exception as exc:  # noqa: BLE001 — the loop must outlive one bad poll
                say(f"relay: poll failed: {type(exc).__name__}: {exc}; retry in {backoff:.0f}s")
                stop.wait(backoff)
                backoff = min(backoff * 2, backoff_cap)
    finally:
        lock.release()
        lock.unwant()
