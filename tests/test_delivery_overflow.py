"""The final-answer overflow path — gist or truncate.

This is what a user actually sees when a reply outgrows a chat platform's
message limit, and it had no tests at all until 2026-07-12. The privacy pin
below is the reason it earned some: `post_gist` was passing `--public`, which
contradicts the data-minimization argument that `kb/design-managed-delivery.md`
uses to justify creating the gist daemon-side in the first place — and
disagreed with the diffense pack gist, which has always been secret.
"""

from __future__ import annotations


from brr.gates import delivery


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fail(_text):  # a gist must not even be attempted under the limit
    raise AssertionError("gist_fn called for a body that fits")


def test_overflow_truncate_measures_utf16_units_not_python_len():
    # U+1F600 (😀) is one Python character but two UTF-16 code units —
    # ``resolve_overflow`` must budget on the platform's own count, not
    # Python's, or a chunk that "fits" here 400s on the wire.
    from brr.channels.telegram import utf16_len

    emoji = "\U0001F600"
    body = emoji * 20  # 20 chars, 40 UTF-16 units — over the limit below
    marker = "\n\n[truncated]"
    out = delivery.resolve_overflow(body, limit=24, gist_fn=lambda _t: None)

    assert utf16_len(out) <= 24
    assert out.endswith(marker)
    assert out == emoji * 5 + marker


