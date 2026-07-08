"""Inline image attachments — resolve markdown/HTML image links embedded
in issue/PR/comment bodies to local files.

GitHub has no separate "attachments" API for issue/PR text: a screenshot
dragged into a comment box becomes a markdown image link baked straight
into the body GitHub already hands the polling loop —
``![description](https://github.com/user-attachments/assets/<uuid>)``.
That URL is never otherwise reachable from inside a sandboxed run (no
browser, no guaranteed arbitrary-network-fetch tool), so this module
downloads it at ingestion time into the same local-file shape Telegram's
photo/document ingestion produces (``gates/telegram.py`` +
``protocol.create_event``'s ``attachment_files``) — one convention, two
gates, so the resident's ``Read`` tool sees an inbound screenshot the
same way regardless of which channel it arrived on.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import client

# Markdown ``![alt](url "title")`` and bare HTML ``<img src="url">`` — the
# two shapes GitHub's own comment editor and a pasted screenshot both
# produce. Deliberately not chasing every possible embed (reference-style
# links, HTML with single-quoted attrs split across lines): those are rare
# enough in practice that a missed one just falls back to the live
# ``github_html_url`` on the event, not a silent wrong answer.
_MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(\s*(https?://[^\s)]+?)(?:\s+"[^"]*")?\s*\)')
_HTML_IMAGE_RE = re.compile(r'<img\b[^>]*?\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)

# Cap per event — a body pasting a dozen screenshots shouldn't turn one
# comment into a dozen downloads; the html_url stays the escape hatch for
# anything past the cap.
_MAX_ATTACHMENTS_PER_EVENT = 6


def extract_image_urls(body: str) -> list[str]:
    """Return image URLs referenced in *body*, de-duplicated, order-preserving."""
    if not body:
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for pattern in (_MD_IMAGE_RE, _HTML_IMAGE_RE):
        for match in pattern.finditer(body):
            url = match.group(1)
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls[:_MAX_ATTACHMENTS_PER_EVENT]


def download_images(token: str, urls: list[str], *, workdir: Path) -> list[Path]:
    """Best-effort download *urls* into fresh files under *workdir*.

    Returns local paths for whichever URLs actually came down — a failed
    one (network hiccup, an expired signed URL, a revoked token) is
    dropped silently rather than blocking event ingestion on it; the
    event's own ``github_html_url`` stays live for the resident to fetch
    by hand when a local copy didn't make it.
    """
    saved: list[Path] = []
    for i, url in enumerate(urls):
        dest = workdir / f"image-{i:02d}{_guess_suffix(url)}"
        if client._download_url(token, url, dest):
            saved.append(dest)
    return saved


def _guess_suffix(url: str) -> str:
    name = Path(url.split("?", 1)[0]).suffix
    if name and len(name) <= 5 and name[1:].isalnum():
        return name
    # GitHub's asset-redirect URLs carry no extension at all (a bare
    # UUID path); default to png rather than leave the file extensionless
    # — Read/viewer tooling generally keys off the suffix, not sniffed
    # content.
    return ".png"
