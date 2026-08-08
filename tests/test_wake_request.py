"""#328/#733 tap-to-request — the daemon-local half: a presence bit
(`src/brr/wake_request.py`), the claim transport (`gates/cloud.py`), and the
publish wiring that mirrors the server's pending tap."""

from __future__ import annotations

import pytest

from brr import wake_request


def _brr(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    return brr_dir


def test_pending_id_roundtrip_and_removal(tmp_path):
    brr_dir = _brr(tmp_path)
    assert wake_request.pending_id(brr_dir) is None

    wake_request.store_pending(
        brr_dir,
        {
            "request_id": "wake_1",
            "profile": "codex-mini",
            "repo_label": "Gurio/brr",
            "environment": "solitary",
        },
    )
    assert wake_request.pending_id(brr_dir) == "wake_1"

    # Server reports nothing pending (canceled or superseded) → mirror clears.
    wake_request.store_pending(brr_dir, None)
    assert wake_request.pending_id(brr_dir) is None


def test_mirror_keeps_only_the_id(tmp_path):
    """#733: the mirror answers "is a tap parked?" and nothing else.

    Profile, repo, environment and park time are all facts the server owns.
    Mirroring them is what let a local copy disagree with its source, so the
    file must not carry them at all — a future reader reaching for
    `wake-request.json` to learn *which* profile was tapped should find
    nothing to be wrong about.
    """
    import json

    brr_dir = _brr(tmp_path)
    wake_request.store_pending(
        brr_dir,
        {
            "request_id": "wake_bit",
            "profile": "codex-mini",
            "repo_label": "Gurio/brr",
            "environment": "solitary",
            "requested_at": "2026-07-25T10:00:00+00:00",
            "status": "pending",
        },
    )
    on_disk = json.loads((brr_dir / "wake-request.json").read_text())
    assert on_disk == {"request_id": "wake_bit"}


def test_store_pending_ignores_malformed_and_incomplete(tmp_path):
    brr_dir = _brr(tmp_path)
    wake_request.store_pending(brr_dir, {"request_id": ""})
    assert wake_request.pending_id(brr_dir) is None
    wake_request.store_pending(brr_dir, {})
    assert wake_request.pending_id(brr_dir) is None
    # Malformed on-disk file reads as no request, never raises.
    (brr_dir / "wake-request.json").write_text("{not json")
    assert wake_request.pending_id(brr_dir) is None


def test_store_pending_has_no_resurrect_guard(tmp_path):
    """#733: the guard existed only because the mirror lagged the daemon's
    own ack, and there is no ack any more.

    A tap this daemon claimed and the server *refused* stays pending at the
    source and must keep mirroring — the old consumed-ledger guard would
    have swallowed it permanently on this daemon.
    """
    brr_dir = _brr(tmp_path)
    wake_request.store_pending(brr_dir, {"request_id": "wake_refused"})
    wake_request.drop_pending(brr_dir)
    assert wake_request.pending_id(brr_dir) is None

    wake_request.store_pending(brr_dir, {"request_id": "wake_refused"})
    assert wake_request.pending_id(brr_dir) == "wake_refused"


def test_consumed_ledger_is_gone(tmp_path):
    """The second ledger and every ack path with it (#733). Named as a
    behaviour so a later "just add a small `-lapsed.json`" reddens here."""
    assert not hasattr(wake_request, "consume")
    assert not hasattr(wake_request, "consumed_ids")
    assert not hasattr(wake_request, "clear_consumed")
    assert not hasattr(wake_request, "lapse")
    assert not hasattr(wake_request, "claimable_for_event")

    brr_dir = _brr(tmp_path)
    wake_request.store_pending(brr_dir, {"request_id": "wake_x"})
    wake_request.drop_pending(brr_dir)
    assert sorted(p.name for p in brr_dir.iterdir()) == []


def test_record_receipt_roundtrip_and_overwrite(tmp_path):
    """Only the latest outcome is live context, so the receipt overwrites."""
    brr_dir = _brr(tmp_path)
    assert wake_request.last_receipt(brr_dir) is None

    wake_request.record_receipt(
        brr_dir, "wake_5", source="telegram", event_id="evt-a", profile="codex-mini",
    )
    receipt = wake_request.last_receipt(brr_dir)
    assert receipt["at"]  # stamped, so a stale receipt is legible as stale
    assert {k: v for k, v in receipt.items() if k != "at"} == {
        "request_id": "wake_5",
        "source": "telegram",
        "event_id": "evt-a",
        "profile": "codex-mini",
    }

    wake_request.record_receipt(
        brr_dir, "wake_6", source="github", event_id="evt-b", profile="claude",
    )
    receipt = wake_request.last_receipt(brr_dir)
    assert {k: v for k, v in receipt.items() if k != "at"} == {
        "request_id": "wake_6",
        "source": "github",
        "event_id": "evt-b",
        "profile": "claude",
    }


def test_refused_receipt_reads_differently_from_a_spend(tmp_path):
    """#733's other half: the distinction has to leave the server.

    A tap that lapsed and a tap that was spent looked identical from the
    machine the tap was parked for. `outcome`/`reason`/`profile=None` is
    what makes "asked for and never happened, here's why" legible.
    """
    brr_dir = _brr(tmp_path)
    wake_request.record_receipt(
        brr_dir, "wake_lapsed", source="telegram", event_id="evt-x",
        profile=None, outcome="refused",
        reason="the tap expired before a wake claimed it",
    )
    receipt = wake_request.last_receipt(brr_dir)
    assert {k: v for k, v in receipt.items() if k != "at"} == {
        "request_id": "wake_lapsed",
        "source": "telegram",
        "event_id": "evt-x",
        "profile": None,
        "outcome": "refused",
        "reason": "the tap expired before a wake claimed it",
    }


def test_record_receipt_ignores_blank_request_id(tmp_path):
    brr_dir = _brr(tmp_path)
    wake_request.record_receipt(brr_dir, "", source="telegram")
    assert wake_request.last_receipt(brr_dir) is None


def test_publish_runners_mirrors_pending_and_sends_no_ack(tmp_path, monkeypatch):
    """The catalog publish mirrors the response's pending tap — and carries
    no `consumed_wake_request_ids` any more (#733): the daemon has nothing
    to acknowledge, because it no longer decides anything."""
    from brr.gates import cloud

    brr_dir = _brr(tmp_path)
    sent: dict = {}

    def _fake_request(base_url, method, path, *, token=None, json=None, params=None, timeout=None, retry=True):
        sent["path"] = path
        sent["json"] = json
        return {
            "profiles": [],
            "default": None,
            "pending_wake_request": {
                "request_id": "wake_new",
                "profile": "claude-haiku",
                "repo_label": "Gurio/brr",
                "environment": "docker",
                "status": "pending",
            },
        }

    monkeypatch.setattr(cloud, "_request", _fake_request)
    monkeypatch.setattr(cloud, "_runners_snapshot", lambda _brr_dir: {"profiles": [], "default": None})

    cloud._publish_runners(brr_dir, None, {"token": "t", "brnrd_url": "https://x"})

    assert sent["path"] == "/v1/daemons/runners"
    assert "consumed_wake_request_ids" not in sent["json"]
    assert wake_request.pending_id(brr_dir) == "wake_new"


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

    brr_dir = _brr(tmp_path)
    captured: dict = {}
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


def _bind(brr_dir, **overrides):
    wake_request.store_sticky(
        brr_dir,
        request_id=overrides.get("request_id", "w1"),
        profile=overrides.get("profile", "claude-haiku"),
        correspondent_key=overrides.get("correspondent_key", "telegram:user-id:1"),
        claimed_at=overrides.get("claimed_at", "2026-08-08T10:00:00+00:00"),
    )


def test_live_sticky_view_renders_a_live_record_with_expiry(tmp_path):
    from datetime import datetime, timezone

    brr_dir = _brr(tmp_path)
    _bind(brr_dir)
    view = wake_request.live_sticky_view(
        brr_dir, 7200, now=datetime(2026, 8, 8, 11, 0, tzinfo=timezone.utc)
    )
    assert view is not None
    assert view["profile"] == "claude-haiku"
    assert view["expires_at"] == "2026-08-08T12:00:00+00:00"
    assert view["correspondent_key"] == "telegram:user-id:1"


def test_live_sticky_view_is_none_for_absent_expired_or_malformed(tmp_path):
    from datetime import datetime, timezone

    brr_dir = _brr(tmp_path)
    at_expiry = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    assert wake_request.live_sticky_view(brr_dir, 7200) is None  # absent
    _bind(brr_dir)
    assert wake_request.live_sticky_view(brr_dir, 7200, now=at_expiry) is None
    # Malformed: garble the stamp on disk; the view declines, and — unlike
    # dispatch — leaves the file alone (lifecycle is dispatch's).
    _bind(brr_dir, claimed_at="not-a-stamp")
    assert wake_request.live_sticky_view(brr_dir, 7200) is None
    assert wake_request.sticky_record(brr_dir) is not None


def test_release_sticky_drops_only_records_claimed_at_or_before_the_ask(tmp_path):
    brr_dir = _brr(tmp_path)
    _bind(brr_dir)
    # A newer record survives an older ask.
    assert wake_request.release_sticky(brr_dir, "2026-08-08T09:59:59+00:00") is False
    assert wake_request.sticky_record(brr_dir) is not None
    # An ask at/after the claim releases.
    assert wake_request.release_sticky(brr_dir, "2026-08-08T10:00:00+00:00") is True
    assert wake_request.sticky_record(brr_dir) is None
    # Nothing to release, unparsable ask: both decline without error.
    assert wake_request.release_sticky(brr_dir, "2026-08-08T10:00:00+00:00") is False
    _bind(brr_dir)
    assert wake_request.release_sticky(brr_dir, "garbage") is False
    assert wake_request.sticky_record(brr_dir) is not None
