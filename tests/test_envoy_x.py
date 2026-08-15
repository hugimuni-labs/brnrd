"""Tests for the X envoy mechanics (``envoy_x.py``) through the real
caller shape — argv in, receipt log / stdout / the wire out.

The ``--help`` case (``test_help_never_reaches_the_wire``) is the
regression pin for the 2026-08-13 incident (buildlog/0001.md's
postscript): the account-local script tweeted the literal string
``"--help"`` because argv was payload and nothing intercepted a flag
before it reached the wire. It must fail if that guard is ever reverted.
The network is never touched here — ``urlopen`` is monkeypatched, per
``AGENTS.md``'s testing discipline (mock ``urlopen``, never the network).
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from brr import envoy_x


# ── fixtures ─────────────────────────────────────────────────────────


def _paths(tmp_path: Path) -> envoy_x.Paths:
    """A ``Paths`` bundle rooted in an arbitrary directory — never a
    hardcoded account home, matching the module's own contract.
    """
    d = tmp_path / "some-account-home" / "envoys"
    d.mkdir(parents=True)
    (d / "x-brnrd-resident.env").write_text("x_Access_Token=tok-fresh\n", encoding="utf-8")
    return envoy_x.Paths.in_dir(d)


class _Response:
    """A minimal ``urlopen`` stand-in: a JSON body, context-manager shaped."""

    def __init__(self, body: dict[str, Any]):
        self._body = json.dumps(body).encode()

    def read(self, *_a, **_kw):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _http_error(code: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.x.com/2/tweets", code, "error", {}, io.BytesIO(body)
    )


def _refusing_urlopen(monkeypatch):
    """Install a urlopen that fails the test if it is ever called."""

    def _fail(*_a, **_kw):
        raise AssertionError("urlopen must not be called")

    monkeypatch.setattr(envoy_x, "urlopen", _fail)


# ── --help: the regression pin ───────────────────────────────────────


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_never_reaches_the_wire(tmp_path, monkeypatch, flag):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        envoy_x.run_post([flag], paths)
    assert "Usage:" in str(exc.value)
    assert not paths.log.exists()


def test_help_wins_even_with_other_args(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    with pytest.raises(SystemExit):
        envoy_x.run_post(["some text", "--help"], paths)


def test_empty_argv_prints_usage_without_the_wire(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        envoy_x.run_post([], paths)
    assert "Usage:" in str(exc.value)


# ── flag-shaped text refusal + the escape hatch ──────────────────────


def test_flag_shaped_text_is_refused(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    with pytest.raises(SystemExit, match="refusing: text starts with"):
        envoy_x.run_post(["--dry-run", "-not-a-real-flag"], paths)


def test_leading_space_escapes_the_flag_refusal(tmp_path, capsys):
    # Regression: the ported check must not re-introduce the account
    # script's original bug, where `.lstrip()` before the `-` test
    # silently defeated the escape hatch its own error message promised.
    paths = _paths(tmp_path)
    envoy_x.run_post(["--dry-run", "--json", " -a deliberately dash-led post"], paths)
    payload = json.loads(capsys.readouterr().out)
    assert payload["would_post"]["text"] == "-a deliberately dash-led post"


# ── dry-run ───────────────────────────────────────────────────────────


def test_dry_run_prints_and_never_touches_the_wire(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    envoy_x.run_post(["hello world", "--dry-run"], paths)
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "hello world" in out
    assert not paths.log.exists()


def test_dry_run_json_shape_carries_the_would_post_payload(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    envoy_x.run_post(["hi", "--dry-run", "--json", "--reply-to", "42"], paths)
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"would_post": {"text": "hi", "reply": {"in_reply_to_tweet_id": "42"}}}


# ── weighted length: X counts twitter-text weight, not len() ──────────
#
# The defect measured live 2026-08-15: a post that passes len() <= 280
# still gets a fieldless 403 from X, because ".py" (and ".sh"/".md"/
# ".io"/".dev"/".ai") is a live TLD and a bare "word.py" token parses as
# a link — charged a flat 23 (TRANSFORMED_URL_LENGTH), not its own
# length. The four cases below are the exact measured probes from the
# ticket, reconstructed at fixed lengths so the arithmetic (273 - 12 +
# 23 == 284) is asserted, not just eyeballed. The network is never
# touched -- these exercise weighted_length()/link_charges() directly,
# never urlopen.


def _padded(token: str, total_len: int) -> str:
    """*token*, a space, then filler to hit exactly *total_len* chars."""
    filler = "b" * (total_len - len(token) - 1)
    text = f"{token} {filler}"
    assert len(text) == total_len
    return text


def test_weighted_length_charges_a_dotted_filename_as_a_link():
    # "x-browser.py" -- .py is Paraguay's ccTLD, so this parses as a URL
    # and is charged the flat 23 instead of its own 12-char weight.
    text = _padded("x-browser.py", 273)
    assert len(text) == 273
    assert envoy_x.weighted_length(text) == 284  # 273 - 12 + 23
    assert envoy_x.link_charges(text) == [("x-browser.py", 23)]


def test_weighted_length_matches_len_when_the_dot_is_removed():
    # Same text, ".py" -> "-py": no dot, no TLD, no link -- weighted
    # length now equals len() exactly, same as the live 200 result.
    text = _padded("x-browser-py", 273)
    assert len(text) == 273
    assert envoy_x.weighted_length(text) == 273
    assert envoy_x.link_charges(text) == []


def test_weighted_length_padded_past_280_still_refuses_without_a_link():
    # The same non-link text, 11 chars longer (284): over the real limit
    # on its own merits, no link involved -- confirms the check isn't
    # solely a link-detector, it's a length check that also sees links.
    text = _padded("x-browser-py", 284)
    assert envoy_x.weighted_length(text) == 284
    assert envoy_x.link_charges(text) == []


def test_weighted_length_280_filler_fits():
    text = "b" * 280
    assert envoy_x.weighted_length(text) == 280


def test_link_charges_ignores_a_dotted_token_that_is_not_a_real_tld():
    # ".log" is not a TLD (checked against the upstream gTLD/ccTLD table)
    # -- stays plain text, no link charge, matching real X behaviour.
    text = "see notes.log for details"
    assert envoy_x.link_charges(text) == []
    assert envoy_x.weighted_length(text) == len(text)


def test_explicit_scheme_always_counts_as_a_link_even_with_a_fake_tld():
    # twitter-text trusts an explicit http(s):// scheme without
    # re-validating the TLD -- "example.notatld" alone wouldn't link,
    # but with a scheme in front it always does.
    text = "see https://example.notatld/path for details"
    charges = envoy_x.link_charges(text)
    assert charges == [("https://example.notatld/path", 23)]


@pytest.mark.parametrize(
    "text,expected_weighted",
    [
        ("plain ascii, nothing dotted", 27),
        ("x-browser.py", 23),  # the whole token is one link, cost 23
        ("x-browser-py", 12),  # no dot after the hyphen swap -> literal
        ("config.log", 10),  # ".log" is not a TLD -> literal
        ("see docs.md now", 15 - 7 + 23),  # ".md" (Moldova) is a live TLD
        ("http://example.zz/x", 23),  # scheme always counts, fake TLD irrelevant
    ],
)
def test_weighted_length_table(text, expected_weighted):
    assert envoy_x.weighted_length(text) == expected_weighted


# ── pre-flight refusal: before any write call, dry-run included ──────


def test_overlength_post_refuses_before_touching_the_wire(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    text = _padded("x-browser.py", 273)  # weighted 284, over the limit
    with pytest.raises(SystemExit) as exc:
        envoy_x.run_post([text], paths)
    msg = str(exc.value)
    assert "284" in msg
    assert "280" in msg
    assert "273" in msg  # the raw len(), named so it isn't confused for the real count
    assert "x-browser.py" in msg
    assert not paths.log.exists()


def test_overlength_dry_run_also_refuses_so_the_preview_is_honest(tmp_path, monkeypatch):
    # The whole point: a post that used to pass --dry-run and then get
    # refused live now refuses at --dry-run too.
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    text = _padded("x-browser.py", 273)
    with pytest.raises(SystemExit, match="284"):
        envoy_x.run_post([text, "--dry-run"], paths)


def test_within_limit_link_text_still_dry_runs_and_names_the_charge(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    envoy_x.run_post(["see x-browser.py", "--dry-run"], paths)
    out = capsys.readouterr().out
    assert "dry-run" in out
    # len() is 16, weighted is 16 - 12 + 23 == 27 -- both should be visible.
    assert "16" in out
    assert "27" in out
    assert "x-browser.py" in out


def test_dry_run_json_shape_is_unaffected_by_weighted_length(tmp_path, monkeypatch, capsys):
    # The --json dry-run payload contract (test above) doesn't grow a
    # weighted-length field -- it's the would-post payload only.
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    envoy_x.run_post(["see x-browser.py", "--dry-run", "--json"], paths)
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"would_post": {"text": "see x-browser.py"}}


# ── reply threading ──────────────────────────────────────────────────


def test_reply_threads_through_reply_to(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        envoy_x, "urlopen", lambda *_a, **_kw: _Response({"data": {"id": "999"}})
    )
    envoy_x.run_post(["thanks!", "--reply-to", "123"], paths)
    out = capsys.readouterr().out
    assert "reply-to 123" in out
    line = json.loads(paths.log.read_text(encoding="utf-8").strip())
    assert line["reply_to"] == "123"
    assert line["text"] == "thanks!"


# ── the receipt trail ────────────────────────────────────────────────


def test_receipt_log_grows_one_line_per_post(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        envoy_x, "urlopen", lambda *_a, **_kw: _Response({"data": {"id": "1"}})
    )
    envoy_x.run_post(["first"], paths)
    envoy_x.run_post(["second"], paths)
    lines = paths.log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["text"] == "first"
    assert json.loads(lines[1])["text"] == "second"


def test_receipt_log_grows_one_line_per_delete_with_action_deleted(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(envoy_x, "urlopen", lambda *_a, **_kw: _Response({"data": {"deleted": True}}))
    envoy_x.run_post(["delete", "555"], paths)
    line = json.loads(paths.log.read_text(encoding="utf-8").strip())
    assert line["action"] == "deleted"
    assert line["id"] == "555"


def test_delete_requires_exactly_one_id(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _refusing_urlopen(monkeypatch)
    with pytest.raises(SystemExit, match="usage: envoy-x post delete"):
        envoy_x.run_post(["delete"], paths)
    with pytest.raises(SystemExit, match="usage: envoy-x post delete"):
        envoy_x.run_post(["delete", "1", "2"], paths)


# ── 401 → refresh → retry once ───────────────────────────────────────


def test_post_401_retries_once_via_the_refresh_script(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    calls: list[str] = []

    def fake_urlopen(req, *_a, **_kw):
        calls.append(req.headers.get("Authorization", ""))
        if len(calls) == 1:
            raise _http_error(401)
        return _Response({"data": {"id": "77"}})

    def fake_run(cmd, **_kw):
        class R:
            stdout = "tok-refreshed\n"

        assert str(paths.refresh) in cmd
        return R()

    monkeypatch.setattr(envoy_x, "urlopen", fake_urlopen)
    monkeypatch.setattr(envoy_x.subprocess, "run", fake_run)

    envoy_x.run_post(["retried post"], paths)

    assert calls == ["Bearer tok-fresh", "Bearer tok-refreshed"]
    line = json.loads(paths.log.read_text(encoding="utf-8").strip())
    assert line["text"] == "retried post"


def test_post_401_twice_gives_up_without_a_second_retry(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    calls: list[str] = []

    def fake_urlopen(req, *_a, **_kw):
        calls.append(req.headers.get("Authorization", ""))
        raise _http_error(401, b"still unauthorized")

    def fake_run(cmd, **_kw):
        class R:
            stdout = "tok-refreshed\n"

        return R()

    monkeypatch.setattr(envoy_x, "urlopen", fake_urlopen)
    monkeypatch.setattr(envoy_x.subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="post failed: HTTP 401"):
        envoy_x.run_post(["never lands"], paths)
    assert len(calls) == 2  # one retry, never a loop
    assert not paths.log.exists()


def test_read_401_retries_once_via_the_refresh_script(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    calls: list[str] = []

    responses = [
        _http_error(401),
        _Response({"data": {"id": "u1", "username": "brnrd_resident",
                             "public_metrics": {"followers_count": 3}}}),
        _Response({"data": []}),
        _Response({"data": []}),
    ]

    def fake_urlopen(req, *_a, **_kw):
        calls.append(1)
        resp = responses.pop(0)
        if isinstance(resp, urllib.error.HTTPError):
            raise resp
        return resp

    def fake_run(cmd, **_kw):
        class R:
            stdout = "tok-refreshed\n"

        return R()

    monkeypatch.setattr(envoy_x, "urlopen", fake_urlopen)
    monkeypatch.setattr(envoy_x.subprocess, "run", fake_run)

    envoy_x.run_read(["--json"], paths)
    assert len(calls) == 4


# ── read: mentions cursor + json shape ───────────────────────────────


def test_read_json_reports_metrics_and_mentions_and_advances_cursor(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    seq = [
        _Response({"data": {"id": "u1", "username": "brnrd_resident",
                             "public_metrics": {"followers_count": 3}}}),
        _Response({"data": [{"id": "10", "author_id": "a1", "text": "hi",
                              "created_at": "2026-08-13T00:00:00Z"}],
                   "includes": {"users": [{"id": "a1", "username": "stranger"}]}}),
        _Response({"data": []}),
    ]

    def fake_urlopen(_req, *_a, **_kw):
        return seq.pop(0)

    monkeypatch.setattr(envoy_x, "urlopen", fake_urlopen)
    envoy_x.run_read(["--json"], paths)
    payload = json.loads(capsys.readouterr().out)
    assert payload["mentions"][0]["id"] == "10"
    assert json.loads(paths.state.read_text(encoding="utf-8"))["since_id"] == "10"


def test_read_all_bypasses_the_cursor(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.state.write_text(json.dumps({"since_id": "999"}), encoding="utf-8")
    seen_params: list[dict] = []

    def fake_urlopen(req, *_a, **_kw):
        seen_params.append(req.full_url)
        if "/mentions" in req.full_url:
            return _Response({"data": []})
        if "/users/me" in req.full_url:
            return _Response({"data": {"id": "u1", "username": "b",
                                        "public_metrics": {}}})
        return _Response({"data": []})

    monkeypatch.setattr(envoy_x, "urlopen", fake_urlopen)
    envoy_x.run_read(["--all", "--json"], paths)
    mention_calls = [u for u in seen_params if "/mentions" in u]
    assert mention_calls and "since_id" not in mention_calls[0]


# ── never a hardcoded account ─────────────────────────────────────────


def test_paths_never_hardcode_an_account(tmp_path):
    a = envoy_x.Paths.in_dir(tmp_path / "account-a")
    b = envoy_x.Paths.in_dir(tmp_path / "account-b")
    assert a.env != b.env
    assert a.log.parent == tmp_path / "account-a"
    assert b.log.parent == tmp_path / "account-b"


def test_main_post_wraps_paths_in_dir(tmp_path, monkeypatch, capsys):
    d = tmp_path / "home"
    d.mkdir()
    (d / "x-brnrd-resident.env").write_text("x_Access_Token=t\n", encoding="utf-8")
    _refusing_urlopen(monkeypatch)
    with pytest.raises(SystemExit):
        envoy_x.main_post(["--help"], d)
