"""What a letter's file is, read from its bytes, and the line a body reads it by.

A connector may type a file ``application/octet-stream`` (the Telegram relay
does, for a photo): his 11349 was a 960x1280 JPEG shown as octet-stream bytes
under the conversation tail, and the body answered the caption without the
page. So the bytes decide the type when the name did not, the blob gets a
sibling with its extension (an image tool opens by extension), and the line
says what the file is and that it is part of the letter.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

HEAD = 64 * 1024
GENERIC = frozenset({"", "application/octet-stream", "binary/octet-stream"})

#: Leading bytes → (mime, extension), first match wins.
_MAGIC: tuple[tuple[bytes, int, str, str], ...] = (
    (b"\xff\xd8\xff", 0, "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "png"),
    (b"GIF87a", 0, "image/gif", "gif"),
    (b"GIF89a", 0, "image/gif", "gif"),
    (b"%PDF-", 0, "application/pdf", "pdf"),
    (b"OggS", 0, "audio/ogg", "ogg"),
    (b"ID3", 0, "audio/mpeg", "mp3"),
    (b"\x1aE\xdf\xa3", 0, "video/webm", "webm"),
    (b"PK\x03\x04", 0, "application/zip", "zip"),
)
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp",
        "image/heic": "heic", "image/avif": "avif", "application/pdf": "pdf", "audio/ogg": "ogg", "audio/mpeg": "mp3",
        "audio/wav": "wav", "audio/mp4": "m4a", "video/mp4": "mp4", "video/quicktime": "mov",
        "video/webm": "webm", "application/zip": "zip", "text/plain": "txt"}


def sniff(head: bytes) -> str:
    """The MIME type the leading bytes show, or ``""``."""
    for magic, at, mime, _ext in _MAGIC:
        if head[at:at + len(magic)] == magic:
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "image/avif"
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return "image/heic"
        if brand == b"qt  ":
            return "video/quicktime"
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4"
        return "video/mp4"
    return ""


def size_of(mime: str, head: bytes) -> tuple[int, int] | None:
    """An image's width and height from its header, or None."""
    try:
        if mime == "image/png" and head[12:16] == b"IHDR":
            return struct.unpack(">II", head[16:24])
        if mime == "image/gif":
            return struct.unpack("<HH", head[6:10])
        if mime == "image/webp":
            chunk = head[12:16]
            if chunk == b"VP8X":
                return (int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1)
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                return (w & 0x3FFF, h & 0x3FFF)
            if chunk == b"VP8L":
                bits = int.from_bytes(head[21:25], "little")
                return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(head):
                if head[i] != 0xFF:
                    i += 1
                    continue
                marker = head[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                    i += 2 if marker != 0xFF else 1
                    continue
                length = struct.unpack(">H", head[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", head[i + 5:i + 9])
                    return (w, h)
                i += 2 + length
    except struct.error:
        return None
    return None


def word(mime: str) -> str:
    """What a person would call the file."""
    top = mime.partition("/")[0]
    if top == "image":
        return "photo"
    if mime == "audio/ogg":
        return "voice message"
    if top == "audio":
        return "audio"
    if top == "video":
        return "video"
    if mime == "application/pdf":
        return "PDF document"
    return "file"


def _human(size: int) -> str:
    for unit, scale in (("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} bytes"


def named(blob: Path, ext: str) -> Path:
    """``<sha>.<ext>`` beside the blob, a hard link (a symlink where links fail).

    The bare blob stays: verify and ``attach`` name blobs by sha. Never raises;
    with no sibling the bare path is the answer."""
    if not ext:
        return blob
    link = blob.with_name(f"{blob.name}.{ext}")
    if link.exists():
        return link
    try:
        os.link(blob, link)
    except FileExistsError:
        pass
    except OSError:
        try:
            link.symlink_to(blob.name)
        except FileExistsError:
            pass
        except OSError:
            return blob
    return link


def line(blob: Path, mime: str, size: int) -> str:
    """The attachment line: what the file is, where it opens, and that it is the letter's."""
    head = b""
    try:
        with blob.open("rb") as stream:
            head = stream.read(HEAD)
    except OSError:
        pass
    seen = sniff(head)
    if mime in GENERIC or (seen and seen.partition("/")[0] != mime.partition("/")[0]):
        mime = seen or mime or "application/octet-stream"
    dims = size_of(mime, head)
    what = word(mime)
    shape = f"{dims[0]}x{dims[1]}" if dims else ""
    path = named(blob, _EXT.get(mime, "")) if blob.exists() else blob
    detail = " · ".join(part for part in (mime, shape, _human(size)) if part)
    return f"attachment: {what} ({detail}) · {path} · part of this letter: open it before answering"


def too_large(number: int, size: int | None) -> str:
    """A file past the connector's cap: it is not lost, it is too big, and the letter says so."""
    shown = f" ({_human(size)})" if size else ""
    return (f"attachment {number}{shown} is past the size the connector may fetch and did not come: "
            "ask for it smaller, or as a link")


def path_of(text: str) -> str:
    """The file an attachment line names, or ``""``."""
    text = text.strip()
    if not text.startswith("attachment") or ": " not in text or " · " not in text:
        return ""
    parts = text.split(" · ")
    for part in parts[1:]:
        if part.startswith("/"):
            return part
    return ""


def unopened(room: Path, letter_id: str) -> list[str]:
    """The files of ``letter_id`` no call in this room has touched, by the boundary's lines.

    A call touches a file when its traced ``blobs`` (the home's blob shas its
    whole input named, ``tooltrace.blobs_named``) hold the file's sha, or its
    ``arg`` (a file tool's basename) names it. A room is one strand: a
    reincarnation gets a new room, so a successor is refused once for a file
    its predecessor opened. One retry is the price."""
    from . import tooltrace
    from .port import parse_boundary
    try:
        boundary = parse_boundary((Path(room) / "port" / "in" / "boundary.md").read_text())
    except Exception:  # noqa: BLE001 - a send is never refused for an unreadable boundary
        return []
    letter = next((view for view in boundary.letters if view.id == letter_id), None)
    if letter is None:
        return []
    paths = [path for path in map(path_of, letter.body.splitlines()) if path]
    if not paths:
        return []
    calls = tooltrace.read(Path(room), limit=0)
    def opened(path: str) -> bool:
        name = Path(path).name
        sha = name.partition(".")[0]
        return any(sha in call.get("blobs", ()) or name in call["arg"] for call in calls)
    return [path for path in paths if not opened(path)]
