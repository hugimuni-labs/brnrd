"""Unit tests for ``brnrd.platforms.github.add_reaction`` — the transport
layer for the ":eyes:" summons-acknowledgment reaction. Wiring into the
webhook handlers is covered in ``test_brnrd_github.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

import httpx  # noqa: E402

from brnrd.platforms import github as gh  # noqa: E402


def _fake_post(monkeypatch, status_code: int):
    seen = {}

    def fake_post(url, *, headers, json, timeout):
        seen.update(url=url, headers=headers, json=json, timeout=timeout)
        return httpx.Response(
            status_code, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(gh.httpx, "post", fake_post)
    return seen


@pytest.mark.parametrize(
    ("target", "target_id", "expected_path"),
    [
        ("issue_comment", 100, "/repos/owner/repo/issues/comments/100/reactions"),
        ("issue", 17, "/repos/owner/repo/issues/17/reactions"),
        ("review_comment", 55, "/repos/owner/repo/pulls/comments/55/reactions"),
    ],
)
def test_add_reaction_builds_the_documented_url(
    monkeypatch, target, target_id, expected_path
):
    seen = _fake_post(monkeypatch, 201)

    ok = gh.add_reaction(
        "ghs_token",
        "https://api.github.com",
        "2022-11-28",
        "owner/repo",
        target=target,
        target_id=target_id,
    )

    assert ok is True
    assert seen["url"] == f"https://api.github.com{expected_path}"
    assert seen["json"] == {"content": "eyes"}
    assert seen["headers"]["Authorization"] == "Bearer ghs_token"


@pytest.mark.parametrize("status_code", [200, 201])
def test_add_reaction_accepts_200_and_201(monkeypatch, status_code):
    _fake_post(monkeypatch, status_code)

    assert (
        gh.add_reaction(
            "ghs_token",
            "https://api.github.com",
            "2022-11-28",
            "owner/repo",
            target="issue",
            target_id=17,
        )
        is True
    )


@pytest.mark.parametrize("status_code", [403, 404, 422])
def test_add_reaction_returns_false_without_raising_on_error_status(
    monkeypatch, status_code
):
    _fake_post(monkeypatch, status_code)

    assert (
        gh.add_reaction(
            "ghs_token",
            "https://api.github.com",
            "2022-11-28",
            "owner/repo",
            target="issue",
            target_id=17,
        )
        is False
    )


def test_add_reaction_custom_content(monkeypatch):
    seen = _fake_post(monkeypatch, 201)

    gh.add_reaction(
        "ghs_token",
        "https://api.github.com",
        "2022-11-28",
        "owner/repo",
        target="issue_comment",
        target_id=100,
        content="+1",
    )

    assert seen["json"] == {"content": "+1"}


def test_add_reaction_unknown_target_returns_false_without_a_call(monkeypatch):
    called = []
    monkeypatch.setattr(
        gh.httpx, "post", lambda *a, **k: called.append(True)
    )

    assert (
        gh.add_reaction(
            "ghs_token",
            "https://api.github.com",
            "2022-11-28",
            "owner/repo",
            target="not-a-real-target",
            target_id=1,
        )
        is False
    )
    assert called == []
