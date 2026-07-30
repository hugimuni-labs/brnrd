"""Pure parsers — URL → owner/repo, JSON → meta dicts, mention filtering.

No transport, no I/O. Brnrd-reusable: the managed backend's webhook
receiver normalises payloads to the same event shape produced here, so
keeping these parsers in one place keeps the two sides honest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .constants import _GITHUB_HOSTS, _HTTPS_RE, _ISSUE_URL_RE, _PR_URL_RE, _SSH_RE

# #879 member 5 — one fence-aware mention predicate shared by both ingress
# paths (the gate's polling loop and brnrd's webhook receiver), replacing
# two hand-rolled, already-drifted copies: the gate's case-sensitive
# ``mention not in body`` and the cloud's un-fenced
# ``mention.casefold() in body.casefold()``.
_FENCED_BLOCK_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_BOUNDARY_CHARS = "A-Za-z0-9_-"


def _strip_quoted_and_code(body: str) -> str:
    """Drop fenced code blocks, inline code spans, and ``>`` blockquote lines.

    A handle written to *discuss* the trigger (inside a fence or inline code
    span) or *quoted* from someone else's message (a blockquote line) must
    not itself count as a live mention — see issue #879 member 5.
    """
    text = _FENCED_BLOCK_RE.sub("", body or "")
    text = _INLINE_CODE_RE.sub("", text)
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(">")
    )


def find_mention(body: str, candidates: Iterable[str]) -> str | None:
    """Return the first *candidates* entry mentioned in *body*, or ``None``.

    Fence/inline-code/blockquote-aware (``_strip_quoted_and_code``),
    case-insensitive, and word-boundary matched so a search for
    ``@brnrd-bot`` does not match text containing ``@brnrd-bot-2``.
    *candidates* are matched exactly as given — callers format their own
    leading ``@`` (or not: a configured gate trigger need not be
    ``@``-shaped, see ``_login_to_skip_for_mention_trigger``).
    """
    folded = _strip_quoted_and_code(body).casefold()
    for candidate in candidates:
        needle = str(candidate or "").strip()
        if not needle:
            continue
        pattern = re.compile(
            rf"(?<![{_BOUNDARY_CHARS}]){re.escape(needle.casefold())}(?![{_BOUNDARY_CHARS}])"
        )
        if pattern.search(folded):
            return candidate
    return None


def parse_origin_url(url: str) -> str | None:
    """Return ``owner/name`` for a github.com remote URL, or ``None``."""
    if not url:
        return None
    url = url.strip()
    m = _SSH_RE.match(url)
    if m and m.group(1) in _GITHUB_HOSTS:
        return f"{m.group(2)}/{m.group(3)}"
    m = _HTTPS_RE.match(url)
    if m and m.group(1) in _GITHUB_HOSTS:
        return f"{m.group(2)}/{m.group(3)}"
    return None


def _extract_issue_number(issue_url: str) -> int | None:
    if not issue_url:
        return None
    m = _ISSUE_URL_RE.search(issue_url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _extract_pr_number(pull_request_url: str) -> int | None:
    if not pull_request_url:
        return None
    m = _PR_URL_RE.search(pull_request_url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _format_event_body(title: str, body: str) -> str:
    if title and body:
        return f"# {title}\n\n{body}".strip() + "\n"
    if title:
        return f"# {title}\n"
    return body.strip() + "\n" if body else ""


def _format_review_comment_body(path: str, line: object, body: str) -> str:
    """Prefix inline review context so the worker knows which hunk was tagged."""
    text = body.strip()
    if path:
        loc = f"`{path}`"
        if isinstance(line, int):
            loc += f" line {line}"
        header = f"On {loc}:\n\n"
        return (header + text + "\n") if text else header
    return _format_event_body("", body)


def _login_to_skip_for_mention_trigger(mention: str, token_login: str) -> str | None:
    """GitHub login whose issue/PR comments never emit *mention* events.

    ``bot_login`` in state is the authenticated token owner (often a human
    PAT). Skipping ``author == token_login`` would drop every @-mention the
    operator writes. When the trigger is ``@handle``-shaped, only ``handle``
    is skipped (the automation account named in the mention). For custom
    substring triggers without a leading ``@…`` handle, fall back to the
    token login so legacy ``HELPME``-style triggers still avoid self-loops.
    """
    raw = (mention or "").strip()
    if raw.startswith("@"):
        acc: list[str] = []
        for ch in raw[1:]:
            if ch in " \t\n\r":
                break
            if ch.isalnum() or ch == "-":
                acc.append(ch)
            else:
                break
        login = "".join(acc).strip("-")
        if login:
            return login
    tl = (token_login or "").strip()
    return tl or None


def _skip_mention_comment_author(author: str, mention: str, token_login: str) -> bool:
    skip = _login_to_skip_for_mention_trigger(mention, token_login)
    if not skip or not author:
        return False
    return author.casefold() == skip.casefold()
