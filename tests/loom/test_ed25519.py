"""The button's verify: RFC 8032 vectors pass, and every malformed input is refused."""

from __future__ import annotations

import pytest

from brr.loom.runtime import ed25519
from brr.loom.runtime.ed25519 import verify

# RFC 8032, section 7.1, TEST 1 to TEST 3: (public key, message, signature).
VECTORS = [
    (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


def _v(i: int) -> tuple[bytes, bytes, bytes]:
    pk, msg, sig = VECTORS[i]
    return bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig)


def _off_curve_y() -> bytes:
    """A y with no x on the curve, so any key or R encoding it is not a point."""
    for y in range(2, 100):
        enc = y.to_bytes(32, "little")
        if ed25519._decode(enc) is None:
            return enc
    raise AssertionError("no off-curve y below 100")


@pytest.mark.parametrize("i", range(len(VECTORS)))
def test_rfc8032_vectors_verify(i: int) -> None:
    pk, msg, sig = _v(i)
    assert verify(pk, msg, sig)


@pytest.mark.parametrize("i", range(len(VECTORS)))
def test_one_changed_byte_anywhere_is_refused(i: int) -> None:
    pk, msg, sig = _v(i)
    for pos in (0, 31, 32, 63):
        bad = bytearray(sig)
        bad[pos] ^= 0x01
        assert not verify(pk, msg, bytes(bad))
    assert not verify(pk, msg + b"\x00", sig)
    bad_pk = bytearray(pk)
    bad_pk[0] ^= 0x01
    assert not verify(bytes(bad_pk), msg, sig)


def test_a_signature_under_another_key_is_refused() -> None:
    pk1, _, _ = _v(0)
    _, msg2, sig2 = _v(1)
    assert not verify(pk1, msg2, sig2)


def test_s_not_below_the_group_order_is_refused() -> None:
    # S + L is the same scalar mod L, so a lax verify accepts it (malleability).
    pk, msg, sig = _v(0)
    s = int.from_bytes(sig[32:], "little")
    assert s < ed25519._L
    malleated = sig[:32] + (s + ed25519._L).to_bytes(32, "little")
    assert not verify(pk, msg, malleated)


def test_a_key_not_on_the_curve_is_refused() -> None:
    _, msg, sig = _v(0)
    assert not verify(_off_curve_y(), msg, sig)


def test_an_r_not_on_the_curve_is_refused() -> None:
    pk, msg, sig = _v(0)
    assert not verify(pk, msg, _off_curve_y() + sig[32:])


def test_a_non_canonical_y_is_refused() -> None:
    _, msg, sig = _v(0)
    # y = p encodes the same field element as y = 0, which is a valid point.
    assert not verify(ed25519._P.to_bytes(32, "little"), msg, sig)


@pytest.mark.parametrize("n", [0, 31, 33, 64])
def test_a_key_of_the_wrong_length_is_refused(n: int) -> None:
    pk, msg, sig = _v(0)
    assert not verify((pk * 3)[:n], msg, sig)


@pytest.mark.parametrize("n", [0, 32, 63, 65])
def test_a_signature_of_the_wrong_length_is_refused(n: int) -> None:
    pk, msg, sig = _v(0)
    assert not verify(pk, msg, (sig * 2)[:n])


def test_non_bytes_is_refused_not_raised() -> None:
    pk, msg, sig = _v(0)
    assert not verify(pk.hex(), msg, sig)  # type: ignore[arg-type]
    assert not verify(pk, msg, None)  # type: ignore[arg-type]
