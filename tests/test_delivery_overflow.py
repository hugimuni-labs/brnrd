"""The final-answer overflow path — gist or truncate.

This is what a user actually sees when a reply outgrows a chat platform's
message limit, and it had no tests at all until 2026-07-12. The privacy pin
below is the reason it earned some: `post_gist` was passing `--public`, which
contradicts the data-minimization argument that `kb/design-managed-delivery.md`
uses to justify creating the gist daemon-side in the first place — and
disagreed with the diffense pack gist, which has always been secret.
"""

from __future__ import annotations

import subprocess

from brr.gates import delivery


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_body_within_limit_is_untouched():
    assert delivery.resolve_overflow("short", limit=10, gist_fn=_fail) == "short"


def _fail(_text):  # a gist must not even be attempted under the limit
    raise AssertionError("gist_fn called for a body that fits")


def test_overflow_offloads_to_a_gist_link():
    body = "x" * 50
    out = delivery.resolve_overflow(
        body, limit=10, gist_fn=lambda _t: "https://gist.github.com/u/abc"
    )
    assert out == "Result: https://gist.github.com/u/abc"


def test_overflow_truncates_when_the_gist_cannot_be_made():
    out = delivery.resolve_overflow("x" * 50, limit=10, gist_fn=lambda _t: None)
    assert out == "x" * 10 + "\n\n[truncated]"
    assert len(out) <= 10 + len("\n\n[truncated]")


def test_post_gist_creates_a_secret_gist(monkeypatch):
    """An overflowed answer is not published to the user's public profile.

    Secret still means unlisted-but-URL-reachable, so the chat link works;
    what it does not do is put an agent's spilled context on a public page.
    """
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return _Result(0, "https://gist.github.com/u/abc\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    url = delivery.post_gist("secret-ish content", filename="answer.md")

    assert url == "https://gist.github.com/u/abc"
    assert "--public" not in seen["cmd"]
    assert seen["cmd"][:3] == ["gh", "gist", "create"]
    assert "answer.md" in seen["cmd"]
    assert seen["input"] == "secret-ish content"


def test_post_gist_returns_none_without_gh(monkeypatch):
    def missing(*_a, **_k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing)
    assert delivery.post_gist("body") is None


def test_post_gist_returns_none_when_gh_fails(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _Result(1, "", "boom"))
    assert delivery.post_gist("body") is None
