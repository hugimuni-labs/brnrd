"""#328/#733 tap-to-request — the daemon-local half: a presence bit
(`src/brr/wake_request.py`), the claim transport (`gates/cloud.py`), and the
publish wiring that mirrors the server's pending tap."""

from __future__ import annotations

import pytest



def _brr(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    return brr_dir


# --- the claim transport (#733) ---------------------------------------------


def _claim_stub(captured: dict, result: dict):
    def _fake(base_url, method, path, *, token=None, json=None, params=None, timeout=None, retry=True):
        captured["base_url"] = base_url
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        captured["timeout"] = timeout
        captured["retry"] = retry
        return result

    return _fake


def test_claim_posts_the_whole_question_once(tmp_path, monkeypatch):
    from brr.gates import cloud
    from brr import runner

    brr_dir = _brr(tmp_path)
    captured: dict = {}
    monkeypatch.setattr(runner, "available_runner_catalog", lambda root: [
        {"name": "codex-newly-discovered"}, {"name": "claude-fable"},
    ])
    monkeypatch.setattr(cloud, "_load_state", lambda _d: {"token": "t", "brnrd_url": "https://x"})
    monkeypatch.setattr(
        cloud, "_request",
        _claim_stub(captured, {"apply": True, "reason": None, "request_id": "w1",
                               "status": "consumed", "profile": "codex"}),
    )

    verdict = cloud.claim_wake_request(
        brr_dir, request_id="w1", event_id="evt-1", source="telegram",
        event_created="2026-07-25T10:00:00+00:00",
    )

    assert verdict["apply"] is True
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/daemons/runners/wake-request/claim"
    sent = dict(captured["json"])
    # ``daemon_now`` is this daemon's clock at claim time — a reading, so
    # pinned for shape and presence rather than value. It is load-bearing:
    # without it the server skips the parked-after-the-event rung entirely.
    stamped = sent.pop("daemon_now")
    assert stamped.endswith("Z") and len(stamped) == 20
    assert sent == {
        "request_id": "w1",
        "event_id": "evt-1",
        "source": "telegram",
        "event_created": "2026-07-25T10:00:00+00:00",
        "known_profiles": ["codex-newly-discovered", "claude-fable"],
    }


def test_claim_is_bounded_and_does_not_ride_the_gateway_retry(tmp_path, monkeypatch):
    """~2s is the bound dispatch can afford. The 502/503/504 retry ladder
    would turn a deploy-window blip into ~9s of held dispatch, which is more
    than a tap is worth."""
    from brr.gates import cloud

    brr_dir = _brr(tmp_path)
    captured: dict = {}
    monkeypatch.setattr(cloud, "_load_state", lambda _d: {"token": "t", "brnrd_url": "https://x"})
    monkeypatch.setattr(cloud, "_request", _claim_stub(captured, {"apply": False}))

    cloud.claim_wake_request(brr_dir, request_id="w1")

    assert captured["timeout"] == pytest.approx(2.0)
    assert captured["retry"] is False


def test_claim_fails_open_on_transport_error(tmp_path, monkeypatch):
    """Timeout / connection error / non-2xx ⇒ None ⇒ dispatch proceeds with
    the configured profile, byte-identical to the empty-mirror path."""
    from brr.gates import cloud

    brr_dir = _brr(tmp_path)
    monkeypatch.setattr(cloud, "_load_state", lambda _d: {"token": "t", "brnrd_url": "https://x"})

    def _boom(*args, **kwargs):
        raise RuntimeError("brnrd 502")

    monkeypatch.setattr(cloud, "_request", _boom)
    assert cloud.claim_wake_request(brr_dir, request_id="w1") is None


def test_claim_without_a_connected_account_never_calls_out(tmp_path, monkeypatch):
    from brr.gates import cloud

    brr_dir = _brr(tmp_path)
    monkeypatch.setattr(cloud, "_load_state", lambda _d: {})

    def _never(*args, **kwargs):
        raise AssertionError("a local-only account must not reach the network")

    monkeypatch.setattr(cloud, "_request", _never)
    assert cloud.claim_wake_request(brr_dir, request_id="w1") is None
    assert cloud.claim_wake_request(brr_dir, request_id="") is None


# ── #932 made visible: live_sticky_view + release_sticky (2026-08-08) ────


def test_claim_with_runners_publication_disabled_omits_catalog(tmp_path, monkeypatch):
    from brr import runner
    from brr.gates import cloud, cloud_publisher

    brr_dir = _brr(tmp_path)
    captured = {}
    monkeypatch.setattr(cloud, "_load_state", lambda _d: {"token": "t", "brnrd_url": "https://x"})
    monkeypatch.setattr(cloud_publisher, "_publish_config", lambda _d: {"publish.layers": "none"})
    monkeypatch.setattr(cloud, "_request", _claim_stub(captured, {"apply": True}))
    def forbidden(_root):
        raise AssertionError("a denied catalog must not be collected")
    monkeypatch.setattr(runner, "available_runner_catalog", forbidden)
    assert cloud.claim_wake_request(brr_dir, request_id="w1")["apply"]
    assert "known_profiles" not in captured["json"]
