"""Bearer-token lookup hashing — stdlib only.

Tokens are high-entropy random strings, so a fast single SHA-256
is sufficient for lookup hashing.
"""

from __future__ import annotations

import hashlib


def hash_token(raw: str) -> str:
    """Lookup hash for a bearer token. Stored; never reversed."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
