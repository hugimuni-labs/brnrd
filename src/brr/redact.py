"""Mask secret-bearing values in a command line or a summary string.

Kept apart from ``brr.hooks`` so a per-call hook (the loom's tool trace)
imports these patterns without the quarter second ``brr.hooks`` costs.
"""

from __future__ import annotations

import re

# Patterns where group 1 is the key/prefix and the value follows (redact
# value, preserve key for context).
_SECRET_PATTERNS_KEYED: list[re.Pattern[str]] = [
    # Authorization: Bearer TOKEN  /  Cookie: session=VALUE
    # Value runs to the next quote character or end of line (covers multi-word
    # schemes like "Bearer TOKEN" and "Basic BASE64BLOB").
    re.compile(r"(?i)((?:Authorization|Cookie)\s*:\s*)[^'\"\n]+"),
    # token=VALUE  secret=VALUE  password=VALUE  api_key=VALUE  etc.
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
        r"token|secret|password|passwd|credential|auth)\s*[=:]\s*)\S+"
    ),
    # --token VALUE  --key VALUE  --secret VALUE  --password VALUE  etc.
    re.compile(
        r"(?i)(--(?:token|key|secret|password|api-key|apikey|auth-token)\s+)\S+"
    ),
]

# Patterns where the whole match is the credential (no useful prefix to keep).
_SECRET_PATTERNS_WHOLE: list[re.Pattern[str]] = [
    # JWT: three base64url-encoded segments separated by dots.
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    # Known API-key prefixes (Anthropic, GitHub, GitLab, Slack, AWS).
    re.compile(
        r"(?:sk-ant-|sk-[a-zA-Z0-9]{2}-|ghp_|ghs_|gho_|glpat-|"
        r"xoxb-|xapp-|xoxa-|xoxe-|xoxp-|xoxr-|AKIA)[A-Za-z0-9_\-]{8,}"
    ),
]

_REDACT_PLACEHOLDER = "<redacted>"

def redact_detail(text: str) -> str:
    """Mask secret-bearing values in a command-line or summary string.

    Over-masks rather than under-masks — a value that *looks* like a token is
    masked even if it might not be one.  The ``detail`` field is diagnostic,
    not operational: losing a few non-secret values is cheaper than leaking
    one real credential.
    """
    for pattern in _SECRET_PATTERNS_KEYED:
        text = pattern.sub(lambda m: m.group(1) + _REDACT_PLACEHOLDER, text)
    for pattern in _SECRET_PATTERNS_WHOLE:
        text = pattern.sub(_REDACT_PLACEHOLDER, text)
    return text
