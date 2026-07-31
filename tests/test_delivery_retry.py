"""What a failing delivery is allowed to cost — the 2026-07-31 flood.

One over-long reply, a receiver that answered 500 *after* it had already
forwarded to Telegram, and a retry loop with no memory. Nine hours, 1,230
attempts at the 25-second poll cadence, 1,230 secret gists minted on the
user's GitHub, and no wake surface carrying any of it. A second event, missing
the field that addresses it, had been retried the same way for 36 hours.

Four properties, each pinned below:

- an offload is **idempotent** — a retry reuses the gist it already made;
- because it is idempotent the retried body is **byte-identical**, which is
  the only thing that lets the receiver's own ``sha256`` retry-dedupe fire;
- a failure that **cannot** succeed closes the event instead of looping;
- a failure that **might** succeed backs off, and is visible while it does.
"""

from __future__ import annotations

import json

import pytest

from brr import protocol
from brr.gates import delivery, runtime


@pytest.fixture(autouse=True)
def _clean_backoff():
    runtime._delivery_retry.clear()
    yield
    runtime._delivery_retry.clear()


def _event(tmp_path, body: str, **meta):
    inbox = tmp_path / "inbox"
    responses = tmp_path / "responses"
    inbox.mkdir(exist_ok=True)
    responses.mkdir(exist_ok=True)
    path = protocol.create_event(inbox, source="cloud", body="ask", **meta)
    event = protocol._read_event(path)
    protocol.set_status(event, "done")
    (responses / f"{event['id']}.md").write_text(body, encoding="utf-8")
    return inbox, responses, event


# ── the offload is idempotent ────────────────────────────────────────


def test_a_retried_overflow_reuses_the_gist_it_already_minted(tmp_path):
    cache = delivery.OverflowCache(tmp_path, "cloud")
    minted = []

    def gist(text):
        minted.append(text)
        return f"https://gist.github.com/u/{len(minted)}"

    body = "x" * 9000
    first = delivery.resolve_overflow(body, limit=4096, gist_fn=gist, cache=cache)
    second = delivery.resolve_overflow(body, limit=4096, gist_fn=gist, cache=cache)

    assert first == second, "a retry must post the same bytes, or dedupe cannot fire"
    assert len(minted) == 1, "one reply, one gist — not one gist per attempt"


def test_without_a_cache_every_attempt_still_mints(tmp_path):
    # The pre-fix behaviour, kept explicit: this is what 1,230 gists looked
    # like, and it is why the cache is not an optimisation.
    minted = []

    def gist(text):
        minted.append(text)
        return f"https://gist.github.com/u/{len(minted)}"

    body = "x" * 9000
    first = delivery.resolve_overflow(body, limit=4096, gist_fn=gist)
    second = delivery.resolve_overflow(body, limit=4096, gist_fn=gist)
    assert first != second
    assert len(minted) == 2


def test_a_different_reply_gets_its_own_gist(tmp_path):
    cache = delivery.OverflowCache(tmp_path, "cloud")
    urls = iter(["https://g/1", "https://g/2"])
    a = delivery.resolve_overflow(
        "a" * 9000, limit=4096, gist_fn=lambda _t: next(urls), cache=cache
    )
    b = delivery.resolve_overflow(
        "b" * 9000, limit=4096, gist_fn=lambda _t: next(urls), cache=cache
    )
    assert a != b


def test_the_cache_is_bounded(tmp_path):
    cache = delivery.OverflowCache(tmp_path, "cloud")
    for i in range(delivery.OverflowCache.LIMIT + 20):
        cache.put(f"body-{i}", f"https://g/{i}")
    stored = json.loads(cache.path.read_text(encoding="utf-8"))
    assert len(stored) == delivery.OverflowCache.LIMIT


def test_an_unreadable_cache_degrades_to_minting(tmp_path):
    cache = delivery.OverflowCache(tmp_path, "cloud")
    cache.path.parent.mkdir(parents=True, exist_ok=True)
    cache.path.write_text("{ not json", encoding="utf-8")
    out = delivery.resolve_overflow(
        "x" * 9000, limit=4096, gist_fn=lambda _t: "https://g/1", cache=cache
    )
    assert out == "Result: https://g/1"


# ── the body a retry posts is the same body ──────────────────────────


def test_two_delivery_attempts_of_one_overlong_reply_post_identical_bodies(tmp_path):
    """The property the receiver's dedupe depends on, driven end to end.

    ``inbox.record_response`` recognises a retried terminal post by
    ``sha256(body)``. Before the cache, the body carried a freshly minted gist
    URL on every attempt, so that guard — written after two earlier delivery
    incidents — could never match for any reply that overflowed.
    """
    inbox, responses, _ = _event(
        tmp_path, "y" * 9000, cloud_event_id="ev_1", cloud_platform="telegram"
    )
    cache = delivery.OverflowCache(tmp_path, "cloud")
    minted = iter(f"https://g/{i}" for i in range(10))
    posted: list[str] = []

    def deliver(_event, body):
        body = delivery.resolve_overflow(
            body, limit=4096, gist_fn=lambda _t: next(minted), cache=cache
        )
        posted.append(body)
        raise RuntimeError("500 Internal Server Error")  # forwarded, then failed

    runtime.deliver_responses(inbox, responses, "cloud", deliver, brr_dir=tmp_path)
    runtime._delivery_retry.clear()  # ignore backoff; this test is about bytes
    runtime.deliver_responses(inbox, responses, "cloud", deliver, brr_dir=tmp_path)

    assert len(posted) == 2
    assert posted[0] == posted[1]


# ── permanent vs transient ───────────────────────────────────────────


def test_an_unaddressable_event_is_closed_not_retried(tmp_path):
    inbox, responses, event = _event(tmp_path, "short reply")
    calls = []

    def deliver(_event, _body):
        calls.append(1)
        raise runtime.PermanentDeliveryError("no cloud_event_id")

    runtime.deliver_responses(inbox, responses, "cloud", deliver, brr_dir=tmp_path)
    assert len(calls) == 1
    assert protocol._read_event(event["_path"])["status"] == "error"

    # And a second pass finds nothing to do — the loop is over, not paused.
    runtime.deliver_responses(inbox, responses, "cloud", deliver, brr_dir=tmp_path)
    assert len(calls) == 1


def test_a_transient_failure_backs_off_instead_of_retrying_every_poll(tmp_path):
    inbox, responses, event = _event(tmp_path, "short reply", cloud_event_id="ev_1")
    calls = []

    def deliver(_event, _body):
        calls.append(1)
        raise RuntimeError("500 Internal Server Error")

    for _ in range(5):  # five poll ticks in quick succession
        runtime.deliver_responses(inbox, responses, "cloud", deliver, brr_dir=tmp_path)
    assert len(calls) == 1, "the poll cadence is not the retry cadence"

    # The event is still open: backing off is not giving up.
    assert protocol._read_event(event["_path"])["status"] == "done"


def test_the_backoff_grows_and_is_capped():
    for attempt in range(1, 12):
        runtime._delivery_failed("cloud", "evt-x", now=0.0)
        _, next_at = runtime._delivery_retry[("cloud", "evt-x")]
        assert next_at <= runtime.DELIVERY_BACKOFF_CAP_S
    assert next_at == runtime.DELIVERY_BACKOFF_CAP_S


def test_a_success_clears_the_backoff(tmp_path):
    inbox, responses, _ = _event(tmp_path, "short reply", cloud_event_id="ev_1")
    runtime._delivery_failed("cloud", "evt-x", now=0.0)
    runtime.deliver_responses(
        inbox, responses, "cloud", lambda _e, _b: {"ok": True}, brr_dir=tmp_path
    )
    assert ("cloud", "evt-x") in runtime._delivery_retry  # untouched, different event
    health = runtime.load_health(tmp_path, "cloud")
    assert health.get("delivery_error") is None
    assert health.get("last_delivery_ok")


# ── the failure is readable ──────────────────────────────────────────


def test_a_delivery_failure_reaches_the_gate_health_file(tmp_path):
    inbox, responses, event = _event(tmp_path, "short reply", cloud_event_id="ev_1")

    def deliver(_event, _body):
        raise RuntimeError("brnrd POST /v1/daemons/responses -> 500")

    runtime.deliver_responses(inbox, responses, "cloud", deliver, brr_dir=tmp_path)
    health = runtime.load_health(tmp_path, "cloud")
    assert "500" in health["delivery_error"]
    assert health["delivery_error_event"] == event["id"]
    assert health["delivery_attempts"] == 1


def test_a_successful_poll_does_not_heal_a_delivery_failure(tmp_path):
    """The reason delivery gets its own field rather than ``last_error``.

    ``gate_health_rows`` treats an ingestion error older than the last good
    poll as healed. The cloud gate polls every 25 s and those polls succeed —
    so the nine-hour flood would have been erased from the health surface
    within half a minute of each failure being recorded.
    """
    runtime.record_delivery_health(
        tmp_path, "cloud", event_id="evt-x", error="POST -> 500", attempts=1230
    )
    runtime.record_health(tmp_path, "cloud", ok=True)  # a poll succeeds, after

    (row,) = runtime.gate_health_rows(tmp_path, gates=["cloud"])
    assert row["delivery_error"] == "POST -> 500"
    assert row["delivery_attempts"] == 1230
    assert row["status"] == "degraded"


def test_a_delivered_message_clears_the_health_surface(tmp_path):
    runtime.record_delivery_health(
        tmp_path, "cloud", event_id="evt-x", error="POST -> 500", attempts=3
    )
    runtime.record_delivery_health(tmp_path, "cloud", event_id="evt-x", error=None)
    (row,) = runtime.gate_health_rows(tmp_path, gates=["cloud"])
    assert row["delivery_error"] is None
