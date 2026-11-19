"""Ed25519 signature verification (RFC 8032, section 5.1.7), pure Python.

The button's token is signed by the server's key and checked here with a key
pinned in the package. Verifying needs only public data, so nothing here is
constant time, and the client gains no dependency. There is no signing: the
loom must never be able to make a token, only check one.

Strict where it matters: S must be below the group order, R and A must be
canonical encodings of points on the curve, and the key and signature must
have their exact lengths. The check is cofactorless ([S]B == R + [k]A, by
re-encoding and comparing with R's bytes), as ref10 and OpenSSL do.
"""

from __future__ import annotations

import hashlib

__all__ = ["verify"]

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

# Points are extended coordinates (X, Y, Z, T) with x = X/Z, y = Y/Z, xy = T/Z.
_Point = tuple[int, int, int, int]
_IDENTITY: _Point = (0, 1, 1, 0)


def _add(p: _Point, q: _Point) -> _Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p: _Point) -> _Point:
    q = _IDENTITY
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decode(b: bytes) -> _Point | None:
    n = int.from_bytes(b, "little")
    y = n & ((1 << 255) - 1)
    x = _recover_x(y, n >> 255)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _encode(p: _Point) -> bytes:
    zinv = pow(p[2], _P - 2, _P)
    x = p[0] * zinv % _P
    y = p[1] * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
assert _GX is not None
_B: _Point = (_GX, _GY, 1, _GX * _GY % _P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True only when ``signature`` is a valid Ed25519 signature of ``message``
    under ``public_key``. Never raises on bad input; bad input is False."""
    if not isinstance(public_key, (bytes, bytearray)) or len(public_key) != 32:
        return False
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
        return False
    if not isinstance(message, (bytes, bytearray)):
        return False
    public_key, signature = bytes(public_key), bytes(signature)
    a = _decode(public_key)
    if a is None:
        return False
    r_bytes = signature[:32]
    if _decode(r_bytes) is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(hashlib.sha512(r_bytes + public_key + bytes(message)).digest(), "little") % _L
    # [S]B - [k]A must re-encode to exactly R's bytes.
    neg_a = ((_P - a[0]) % _P, a[1], a[2], (_P - a[3]) % _P)
    return _encode(_add(_mul(s, _B), _mul(k, neg_a))) == r_bytes
