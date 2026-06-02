"""Opaque identifier and secret minting.

Resource ids are prefixed for readability in logs / responses.
Secrets (API keys, daemon tokens, session tokens) are returned to
the client once in plaintext; only their hashes are persisted
(see ``security.py``).
"""

from __future__ import annotations

import secrets

# Pair codes are typed by a human ("/start BR-7F3K"), so the
# alphabet drops easily-confused glyphs (0/O, 1/I/L).
_PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _rid(prefix: str, nbytes: int = 12) -> str:
    return f"{prefix}_{secrets.token_hex(nbytes)}"


def account_id() -> str:
    return _rid("acc")


def project_id() -> str:
    return _rid("proj")


def token_id() -> str:
    return _rid("tok")


def daemon_id() -> str:
    return _rid("dmn")


def event_id() -> str:
    return _rid("ev")


def pair_request_id() -> str:
    return _rid("pair")


def chat_binding_id() -> str:
    return _rid("chat")


def tg_pair_code_id() -> str:
    return _rid("tgpair")


def api_key() -> str:
    return "bk_" + secrets.token_urlsafe(32)


def session_token() -> str:
    return "bs_" + secrets.token_urlsafe(32)


def daemon_token() -> str:
    return "bd_" + secrets.token_urlsafe(32)


def poll_secret() -> str:
    return secrets.token_urlsafe(24)


def pair_code() -> str:
    return "BR-" + "".join(secrets.choice(_PAIR_ALPHABET) for _ in range(4))


def tg_pair_code() -> str:
    # Distinct prefix from the daemon pair code so a `/start TG-…`
    # never collides with a device-flow `BR-…`.
    return "TG-" + "".join(secrets.choice(_PAIR_ALPHABET) for _ in range(4))
