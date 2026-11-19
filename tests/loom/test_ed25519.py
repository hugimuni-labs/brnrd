"""The button's verify: RFC 8032 vectors pass, and every malformed input is refused."""

from __future__ import annotations

import pytest

from brr.loom.runtime import ed25519
from brr.loom.runtime.ed25519 import verify

# RFC 8032, section 7.1: (public key, message, signature).
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
    (  # TEST 1024
        "278117fc144c72340f67d0f2316e8386"
        "ceffbf2b2428c9c51fef7c597f1d426e",
        "08b8b2b733424243760fe426a4b54908"
        "632110a66c2f6591eabd3345e3e4eb98"
        "fa6e264bf09efe12ee50f8f54e9f77b1"
        "e355f6c50544e23fb1433ddf73be84d8"
        "79de7c0046dc4996d9e773f4bc9efe57"
        "38829adb26c81b37c93a1b270b20329d"
        "658675fc6ea534e0810a4432826bf58c"
        "941efb65d57a338bbd2e26640f89ffbc"
        "1a858efcb8550ee3a5e1998bd177e93a"
        "7363c344fe6b199ee5d02e82d522c4fe"
        "ba15452f80288a821a579116ec6dad2b"
        "3b310da903401aa62100ab5d1a36553e"
        "06203b33890cc9b832f79ef80560ccb9"
        "a39ce767967ed628c6ad573cb116dbef"
        "efd75499da96bd68a8a97b928a8bbc10"
        "3b6621fcde2beca1231d206be6cd9ec7"
        "aff6f6c94fcd7204ed3455c68c83f4a4"
        "1da4af2b74ef5c53f1d8ac70bdcb7ed1"
        "85ce81bd84359d44254d95629e9855a9"
        "4a7c1958d1f8ada5d0532ed8a5aa3fb2"
        "d17ba70eb6248e594e1a2297acbbb39d"
        "502f1a8c6eb6f1ce22b3de1a1f40cc24"
        "554119a831a9aad6079cad88425de6bd"
        "e1a9187ebb6092cf67bf2b13fd65f270"
        "88d78b7e883c8759d2c4f5c65adb7553"
        "878ad575f9fad878e80a0c9ba63bcbcc"
        "2732e69485bbc9c90bfbd62481d9089b"
        "eccf80cfe2df16a2cf65bd92dd597b07"
        "07e0917af48bbb75fed413d238f5555a"
        "7a569d80c3414a8d0859dc65a46128ba"
        "b27af87a71314f318c782b23ebfe808b"
        "82b0ce26401d2e22f04d83d1255dc51a"
        "ddd3b75a2b1ae0784504df543af8969b"
        "e3ea7082ff7fc9888c144da2af58429e"
        "c96031dbcad3dad9af0dcbaaaf268cb8"
        "fcffead94f3c7ca495e056a9b47acdb7"
        "51fb73e666c6c655ade8297297d07ad1"
        "ba5e43f1bca32301651339e22904cc8c"
        "42f58c30c04aafdb038dda0847dd988d"
        "cda6f3bfd15c4b4c4525004aa06eeff8"
        "ca61783aacec57fb3d1f92b0fe2fd1a8"
        "5f6724517b65e614ad6808d6f6ee34df"
        "f7310fdc82aebfd904b01e1dc54b2927"
        "094b2db68d6f903b68401adebf5a7e08"
        "d78ff4ef5d63653a65040cf9bfd4aca7"
        "984a74d37145986780fc0b16ac451649"
        "de6188a7dbdf191f64b5fc5e2ab47b57"
        "f7f7276cd419c17a3ca8e1b939ae49e4"
        "88acba6b965610b5480109c8b17b80e1"
        "b7b750dfc7598d5d5011fd2dcc5600a3"
        "2ef5b52a1ecc820e308aa342721aac09"
        "43bf6686b64b2579376504ccc493d97e"
        "6aed3fb0f9cd71a43dd497f01f17c0e2"
        "cb3797aa2a2f256656168e6c496afc5f"
        "b93246f6b1116398a346f1a641f3b041"
        "e989f7914f90cc2c7fff357876e506b5"
        "0d334ba77c225bc307ba537152f3f161"
        "0e4eafe595f6d9d90d11faa933a15ef1"
        "369546868a7f3a45a96768d40fd9d034"
        "12c091c6315cf4fde7cb68606937380d"
        "b2eaaa707b4c4185c32eddcdd306705e"
        "4dc1ffc872eeee475a64dfac86aba41c"
        "0618983f8741c5ef68d3a101e8a3b8ca"
        "c60c905c15fc910840b94c00a0b9d0",
        "0aab4c900501b3e24d7cdf4663326a3a"
        "87df5e4843b2cbdb67cbf6e460fec350"
        "aa5371b1508f9f4528ecea23c436d94b"
        "5e8fcd4f681e30a6ac00a9704a188a03",
    ),
    (  # TEST SHA(abc)
        "ec172b93ad5e563bf4932c70e1245034"
        "c35467ef2efd4d64ebf819683467e2bf",
        "ddaf35a193617abacc417349ae204131"
        "12e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd"
        "454d4423643ce80e2a9ac94fa54ca49f",
        "dc2a4459e7369633a52b1bf277839a00"
        "201009a3efbf3ecb69bea2186c26b589"
        "09351fc9ac90b3ecfdfbc7c66431e030"
        "3dca179c138ac17ad9bef1177331a704",
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
    assert not verify(pk, None, sig)  # type: ignore[arg-type]
    assert not verify(pk.hex(), msg, sig)  # type: ignore[arg-type]
    assert not verify(pk, msg, None)  # type: ignore[arg-type]
