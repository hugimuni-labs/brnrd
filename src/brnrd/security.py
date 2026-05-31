"""Password and bearer-token hashing — stdlib only.

We deliberately avoid bcrypt/argon2 native wheels for the
prototype (per the repo's "avoid native-extension-heavy
packages" guideline) and use PBKDF2-HMAC-SHA256 from hashlib.
Tokens are high-entropy random strings, so a fast single SHA-256
is sufficient for lookup hashing; passwords get the slow KDF.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
    )
    return hmac.compare_digest(dk.hex(), dk_hex)


def hash_token(raw: str) -> str:
    """Lookup hash for a bearer token. Stored; never reversed."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
