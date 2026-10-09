"""One relay poll into the ledger. Event identity survives cursor resets.

This adapter is not armed by the loom yet: the shared-consumer cutover is
slice 3. The injected client exposes ``pull(cursor)`` and
``download_attachment(event_id, index, destination)``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
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


def _person_channel(to: object, chat: object) -> bool:
    # Chat keys are platform-qualified, so Telegram 42 cannot select a
    # WhatsApp thread whose chat happens to be 42.
    return (isinstance(to, str) and to == f"channel:{chat}"
            and to.split(":", 1)[1].split("/", 1)[0] in {"telegram", "slack", "whatsapp"})


def route_bare(facts: list[Fact], chat: str, reply_to_letter: str | None,
               now: float) -> str:
    """Reply binding, then last confirmed speech with a live lease, then inbox.

    ``chat`` is ``<platform>/<chat_id>``. A speech's ``key`` names the
    outbound letter (the existing speak receipt). Thread leases have no
    deadline of their own: their router generation supplies ``until``.
    """
    ordered = union(facts)
    state = fold(ordered)
    threads = sender_threads(state.accepted)
    letters = {
        str(f.data.get("id") or f.id): f
        for f in state.accepted if f.kind == "letter"
    }
    spoken = []
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
        spoken.append((letter, sender, thread))
        if reply_to_letter and reply_to_letter == str(letter.data.get("id") or letter.id):
            return f"thread:{thread}"
    if not spoken:
        return "thread:inbox"
    letter, sender, thread = spoken[-1]
    if state.holder.get(thread) != (sender, letter.data.get("gen")):
        return "thread:inbox"
    lease = next((fact for fact in reversed(state.accepted)
                  if fact.kind == "lease" and fact.data.get("thread") == thread
                  and fact.data.get("strand") == sender
                  and fact.data.get("gen") == letter.data.get("gen")), None)
    if lease is None:
        return "thread:inbox"
    router_gen = lease.data.get("router_gen")
    # A step-1 lease lasts until released. Once router facts exist it is
    # fenced by fold(), just as it is for routing and handling letters.
    if router_gen is None:
        return f"thread:{thread}"
    router_gens = [f.data.get("gen") for f in ordered if f.kind == "router"
                   and isinstance(f.data.get("gen"), int)]
    if not router_gens or max(router_gens) != router_gen:
        return "thread:inbox"
    windows = [f for f in ordered if f.kind in {"router", "router.renewed"}
               and f.data.get("gen") == router_gen]
    until = windows[-1].data.get("until") if windows else None
    if not isinstance(until, (int, float)) or now >= until:
        return "thread:inbox"
    return f"thread:{thread}"


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
        for index in range(len(names)):
            sha, size = _blob(home, client, event_id, index)
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
        # Optional verified metadata seam. Today's Telegram webhook omits it;
        # message_id is the *incoming* id and must never be treated as a reply.
        if platform == "telegram" and origin.get("reply_to_letter"):
            data["reply_to_letter"] = origin["reply_to_letter"]
        # Source is durable before letter. Save the routing decision as part
        # of that intent: HLC ordering alone cannot reconstruct the original
        # snapshot once facts from another install arrive during recovery.
        data["to"] = route_bare(
            facts, f"{platform}/{data['chat']}", data.get("reply_to_letter"), time.time(),
        )
        source = append(home, Fact(
            kind="source", by=f"loom:{home.install_id()}", id=source_id,
            after=tuple(blob_ids), data=data,
        ))
        facts.append(source)
        sources[source_id] = source
        facts.append(_letter(home, source))
        letter_ids.add(letter_id)
    _write_cursor(home, next_cursor)
    return next_cursor
