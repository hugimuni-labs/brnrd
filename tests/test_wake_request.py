"""#328 tap-to-request — daemon-local wake-request file protocol
(`src/brr/wake_request.py`) and its cloud-gate publish wiring."""

from __future__ import annotations

from brr import wake_request


def _brr(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    return brr_dir


def test_pending_roundtrip_and_removal(tmp_path):
    brr_dir = _brr(tmp_path)
    assert wake_request.pending(brr_dir) is None

    wake_request.store_pending(
        brr_dir,
        {
            "request_id": "wake_1",
            "profile": "codex-mini",
            "repo_label": "Gurio/brr",
            "environment": "solitary",
        },
    )
    assert wake_request.pending(brr_dir) == {
        "request_id": "wake_1",
        "profile": "codex-mini",
        "repo_label": "Gurio/brr",
        "environment": "solitary",
    }

    # Server reports nothing pending (canceled or superseded) → mirror clears.
    wake_request.store_pending(brr_dir, None)
    assert wake_request.pending(brr_dir) is None


def test_store_pending_ignores_malformed_and_incomplete(tmp_path):
    brr_dir = _brr(tmp_path)
    wake_request.store_pending(brr_dir, {"request_id": "", "profile": "x"})
    assert wake_request.pending(brr_dir) is None
    wake_request.store_pending(brr_dir, {"request_id": "wake_2"})
    assert wake_request.pending(brr_dir) is None
    # Malformed on-disk file reads as no request, never raises.
    (brr_dir / "wake-request.json").write_text("{not json")
    assert wake_request.pending(brr_dir) is None


def test_consume_moves_id_to_ack_ledger(tmp_path):
    brr_dir = _brr(tmp_path)
    wake_request.store_pending(brr_dir, {"request_id": "wake_3", "profile": "codex"})
    wake_request.consume(brr_dir, "wake_3")

    assert wake_request.pending(brr_dir) is None
    assert wake_request.consumed_ids(brr_dir) == ["wake_3"]

    # The server hasn't processed the ack yet and still returns the same
    # request on the next tick — it must not resurrect.
    wake_request.store_pending(brr_dir, {"request_id": "wake_3", "profile": "codex"})
    assert wake_request.pending(brr_dir) is None

    # Ack processed → ledger clears; a later different request lands fine.
    wake_request.clear_consumed(brr_dir, ["wake_3"])
    assert wake_request.consumed_ids(brr_dir) == []
    wake_request.store_pending(brr_dir, {"request_id": "wake_4", "profile": "codex"})
    assert wake_request.pending(brr_dir)["request_id"] == "wake_4"


def test_record_receipt_roundtrip_and_overwrite(tmp_path):
    """#564: the receipt is a separate file from the ack ledger — it must
    not perturb `consumed_ids()` (that list is wire-format for the
    publish-tick ack) and it overwrites, since only the latest consumption
    is live context."""
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
    # Doesn't touch the ack ledger the publish tick sends over the wire.
    assert wake_request.consumed_ids(brr_dir) == []

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


def test_record_receipt_ignores_blank_request_id(tmp_path):
    brr_dir = _brr(tmp_path)
    wake_request.record_receipt(brr_dir, "", source="telegram")
    assert wake_request.last_receipt(brr_dir) is None


def test_publish_runners_roundtrips_wake_request(tmp_path, monkeypatch):
    """The catalog publish sends consumed acks, clears them on success, and
    mirrors the response's pending request."""
    from brr.gates import cloud

    brr_dir = _brr(tmp_path)
    wake_request.consume(brr_dir, "wake_old")  # pending ack from a prior wake

    sent: dict = {}

    def _fake_request(base_url, method, path, *, token=None, json=None, params=None, timeout=None):
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

    cloud._publish_runners(brr_dir, {"token": "t", "brnrd_url": "https://x"})

    assert sent["path"] == "/v1/daemons/runners"
    assert sent["json"]["consumed_wake_request_ids"] == ["wake_old"]
    assert wake_request.consumed_ids(brr_dir) == []  # acked and cleared
    assert wake_request.pending(brr_dir) == {
        "request_id": "wake_new",
        "profile": "claude-haiku",
        "repo_label": "Gurio/brr",
        "environment": "docker",
    }


def test_publish_runners_failure_keeps_ack_ledger(tmp_path, monkeypatch):
    """A failed publish must not drop the consumed ack — the server would
    keep the row pending forever and re-offer a spent request."""
    from brr.gates import cloud

    brr_dir = _brr(tmp_path)
    wake_request.consume(brr_dir, "wake_kept")

    def _boom(*args, **kwargs):
        raise RuntimeError("brnrd 502")

    monkeypatch.setattr(cloud, "_request", _boom)
    monkeypatch.setattr(cloud, "_runners_snapshot", lambda _brr_dir: {"profiles": [], "default": None})

    cloud._publish_runners(brr_dir, {"token": "t", "brnrd_url": "https://x"})

    assert wake_request.consumed_ids(brr_dir) == ["wake_kept"]
