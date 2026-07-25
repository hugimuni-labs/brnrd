"""#328 tap-to-request — daemon-local wake-request file protocol
(`src/brr/wake_request.py`) and its cloud-gate publish wiring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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

    cloud._publish_runners(brr_dir, None, {"token": "t", "brnrd_url": "https://x"})

    assert sent["path"] == "/v1/daemons/runners"
    assert sent["json"]["consumed_wake_request_ids"] == ["wake_old"]
    assert wake_request.consumed_ids(brr_dir) == []  # acked and cleared
    assert wake_request.pending(brr_dir) == {
        "request_id": "wake_new",
        "profile": "claude-haiku",
        "repo_label": "Gurio/brr",
        "environment": "docker",
    }


def test_store_pending_carries_parked_at_from_requested_at(tmp_path):
    """#577: the server-stamped `requested_at` mirrors through as
    `parked_at` — additive, and only when the payload actually carries it
    (an older server / daemon leaves the shape exactly as before)."""
    brr_dir = _brr(tmp_path)
    parked = datetime.now(timezone.utc).isoformat()
    wake_request.store_pending(
        brr_dir,
        {"request_id": "wake_parked", "profile": "codex", "requested_at": parked},
    )
    assert wake_request.pending(brr_dir) == {
        "request_id": "wake_parked", "profile": "codex", "parked_at": parked,
    }


def test_claimable_for_event_within_window():
    now = datetime.now(timezone.utc)
    parked = now - timedelta(seconds=2)
    request = {"request_id": "w1", "profile": "codex", "parked_at": parked.isoformat()}
    assert wake_request.claimable_for_event(request, now.isoformat()) is True


def test_a_tap_older_than_the_event_is_still_claimable(tmp_path):
    """#733: the direction that ate a real tap.

    #577 refused any tap parked more than 120 s before the event that wanted
    it. A human taps the rack and then composes a message; the maintainer's
    took 16 minutes, and the guard fired on exactly the behaviour the feature
    exists to serve. "Too old" is now the expiry's question and only the
    expiry's, so age alone never disqualifies a claim.
    """
    now = datetime.now(timezone.utc)
    for age in (timedelta(minutes=10), timedelta(hours=6), timedelta(hours=23)):
        request = {
            "request_id": "w2", "profile": "codex",
            "parked_at": (now - age).isoformat(),
        }
        assert wake_request.claimable_for_event(request, now.isoformat()) is True


def test_a_tap_parked_after_the_event_is_not_for_that_event():
    """The half of #577 that survives, because no expiry can answer it.

    A tap minted while a wake is already queued was parked for the *next* one.
    The small tolerance is for a tap and its message being one breath with
    disagreeing clocks.
    """
    now = datetime.now(timezone.utc)

    def _claimable(offset):
        return wake_request.claimable_for_event(
            {"request_id": "w3", "profile": "codex",
             "parked_at": (now + offset).isoformat()},
            now.isoformat(),
        )

    assert _claimable(timedelta(seconds=2)) is True     # same breath, clock skew
    assert _claimable(timedelta(seconds=60)) is False    # a later wake's tap


def test_claimable_for_event_missing_timestamps_defaults_true():
    """No `parked_at` (legacy mirror) or no event `created` ⇒ nothing to
    judge against ⇒ claim whatever is pending, same as before #577 ever
    existed. A parsing hiccup must never silently swallow a tap."""
    assert wake_request.claimable_for_event({"profile": "codex"}, None) is True
    assert wake_request.claimable_for_event(
        {"profile": "codex", "parked_at": datetime.now(timezone.utc).isoformat()},
        None,
    ) is True


def test_expiry_is_the_servers_and_pending_no_longer_decides(tmp_path):
    """#733: one staleness horizon, published, and `pending()` is only a read.

    The old shape kept a local 900 s TTL *inside* `pending()`, so a lapse
    returned `None` and every miss was invisible to the report surface built
    for it. Now the mirror carries the row's own `expires_at`, `expired()` is
    the only thing that judges it, and the tap is still in the caller's hand
    when the verdict is composed.
    """
    brr_dir = _brr(tmp_path)
    now = datetime.now(timezone.utc)
    wake_request.store_pending(brr_dir, {
        "request_id": "wake_live", "profile": "codex",
        "requested_at": (now - timedelta(hours=3)).isoformat(),
        "expires_at": (now + timedelta(hours=21)).isoformat(),
    })
    live = wake_request.pending(brr_dir)
    # Three hours old — dead under the retired 900 s TTL, alive under the row.
    assert live is not None
    assert live["expires_at"]
    assert wake_request.expired(live) is False

    wake_request.store_pending(brr_dir, {
        "request_id": "wake_gone", "profile": "codex",
        "requested_at": (now - timedelta(days=2)).isoformat(),
        "expires_at": (now - timedelta(hours=1)).isoformat(),
    })
    stale = wake_request.pending(brr_dir)
    # Still *returned* — that is the point. The caller decides and can report.
    assert stale is not None and stale["request_id"] == "wake_gone"
    assert wake_request.expired(stale) is True


def test_a_mirror_with_no_expiry_is_never_locally_expired(tmp_path):
    """An older server sends no `expires_at`, so this daemon has no opinion.

    Inventing a local horizon to fill that silence is precisely the bug #733
    removed: the surface that answered was not the surface that decided. When
    the server stops returning the request, `store_pending` drops the mirror —
    staleness stays where it already lives.
    """
    brr_dir = _brr(tmp_path)
    ancient = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    wake_request.store_pending(
        brr_dir,
        {"request_id": "wake_ancient", "profile": "codex", "requested_at": ancient},
    )
    request = wake_request.pending(brr_dir)
    assert request is not None
    assert "expires_at" not in request
    assert wake_request.expired(request) is False


def test_a_lapse_is_acked_as_expired_not_as_a_spend(tmp_path):
    """#733: the two ledgers, and why they cannot be one.

    `lapse()` used to write the same list `consume()` writes, so every expiry
    was published through `mark_consumed` — *"these requests were spent on a
    dispatched wake"* — and the chip flipped to consumed for a tap that never
    ran. Its docstring defended the loss because "the receipt is where the
    distinction lives… a human or the dashboard can tell": the receipt is a
    local file that never leaves the machine.
    """
    brr_dir = _brr(tmp_path)
    wake_request.store_pending(brr_dir, {"request_id": "wake_lapse", "profile": "codex"})
    wake_request.lapse(
        brr_dir, "wake_lapse", source="telegram", event_id="evt-x",
        reason="the tap expired at 2026-07-25T10:55:00+00:00",
    )
    assert wake_request.pending(brr_dir) is None
    # The distinction the wire now carries: expired, not spent.
    assert wake_request.lapsed_ids(brr_dir) == ["wake_lapse"]
    assert wake_request.consumed_ids(brr_dir) == []
    receipt = wake_request.last_receipt(brr_dir)
    assert {k: v for k, v in receipt.items() if k != "at"} == {
        "request_id": "wake_lapse",
        "source": "telegram",
        "event_id": "evt-x",
        "profile": None,
        "outcome": "lapsed",
        "reason": "the tap expired at 2026-07-25T10:55:00+00:00",
    }
    # Doesn't resurrect even if the server still reports it pending: the
    # lapsed ledger blocks it exactly as the consumed one does.
    wake_request.store_pending(brr_dir, {"request_id": "wake_lapse", "profile": "codex"})
    assert wake_request.pending(brr_dir) is None


def test_the_two_ledgers_clear_independently(tmp_path):
    """Acking one must not silently drop the other's pending work."""
    brr_dir = _brr(tmp_path)
    wake_request.store_pending(brr_dir, {"request_id": "spent", "profile": "codex"})
    wake_request.consume(brr_dir, "spent")
    wake_request.store_pending(brr_dir, {"request_id": "gone", "profile": "codex"})
    wake_request.lapse(brr_dir, "gone", source="ttl", reason="expired")
    assert wake_request.consumed_ids(brr_dir) == ["spent"]
    assert wake_request.lapsed_ids(brr_dir) == ["gone"]

    wake_request.clear_consumed(brr_dir, ["spent"])
    assert wake_request.consumed_ids(brr_dir) == []
    assert wake_request.lapsed_ids(brr_dir) == ["gone"]
    wake_request.clear_lapsed(brr_dir, ["gone"])
    assert wake_request.lapsed_ids(brr_dir) == []


def test_record_receipt_default_shape_unaffected_by_new_kwargs():
    """The additive `outcome`/`reason` kwargs must not perturb the payload
    for every pre-#577 caller that never passes them."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        brr_dir = Path(tmp) / ".brr"
        wake_request.record_receipt(
            brr_dir, "wake_plain", source="telegram", event_id="evt-a", profile="codex",
        )
        receipt = wake_request.last_receipt(brr_dir)
        assert {k: v for k, v in receipt.items() if k != "at"} == {
            "request_id": "wake_plain",
            "source": "telegram",
            "event_id": "evt-a",
            "profile": "codex",
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

    cloud._publish_runners(brr_dir, None, {"token": "t", "brnrd_url": "https://x"})

    assert wake_request.consumed_ids(brr_dir) == ["wake_kept"]
