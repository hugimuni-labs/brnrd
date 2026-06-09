"""Tests for the streaming (multi-response) delivery skeleton.

Pins ``runtime.deliver_stream`` — the shared control flow that delivers
interim response partials before the terminal response and cleans up
only on a done event. See ``kb/design-multi-response.md``.
"""

from brr import protocol
from brr.gates import runtime


def _event(inbox, source, body, status):
    protocol.create_event(inbox, source=source, body=body)
    ev = [e for e in protocol.list_pending(inbox) if e["body"] == body][0]
    if status != "pending":
        protocol.set_status(ev, status)
    return ev


def _capture():
    sent: list[str] = []
    return sent, (lambda event, body: sent.append(body))


def test_single_response_backward_compatible(tmp_path):
    inbox, responses = tmp_path / "inbox", tmp_path / "responses"
    ev = _event(inbox, "tg", "task", "done")
    protocol.write_response(responses, ev["id"], "the answer")

    sent, deliver = _capture()
    runtime.deliver_stream(inbox, responses, "tg", deliver)

    assert sent == ["the answer"]
    # event + response cleaned up
    assert not ev["_path"].exists()
    assert not protocol.response_exists(responses, ev["id"])


def test_partials_stream_before_terminal_and_in_order(tmp_path):
    inbox, responses = tmp_path / "inbox", tmp_path / "responses"
    ev = _event(inbox, "tg", "task", "processing")
    protocol.write_partial(responses, ev["id"], "step 1")
    protocol.write_partial(responses, ev["id"], "step 2")

    sent, deliver = _capture()
    runtime.deliver_stream(inbox, responses, "tg", deliver)

    # interim partials delivered, in order; event still alive (not done)
    assert sent == ["step 1", "step 2"]
    assert ev["_path"].exists()
    assert protocol.list_partials(responses, ev["id"]) == []

    # now the thought finishes: terminal delivered, everything cleaned up
    protocol.set_status(ev, "done")
    protocol.write_response(responses, ev["id"], "final")
    runtime.deliver_stream(inbox, responses, "tg", deliver)

    assert sent == ["step 1", "step 2", "final"]
    assert not ev["_path"].exists()
    assert not protocol.partials_dir(responses, ev["id"]).exists()


def test_terminal_callback_used_for_terminal_only(tmp_path):
    inbox, responses = tmp_path / "inbox", tmp_path / "responses"
    ev = _event(inbox, "tg", "task", "done")
    protocol.write_partial(responses, ev["id"], "interim")
    protocol.write_response(responses, ev["id"], "closing")

    partial_msgs: list[str] = []
    terminal_msgs: list[str] = []
    runtime.deliver_stream(
        inbox, responses, "tg",
        lambda e, b: partial_msgs.append(b),
        lambda e, b: terminal_msgs.append(b),
    )

    assert partial_msgs == ["interim"]
    assert terminal_msgs == ["closing"]


def test_partial_delivery_failure_is_resumable(tmp_path):
    inbox, responses = tmp_path / "inbox", tmp_path / "responses"
    ev = _event(inbox, "tg", "task", "processing")
    protocol.write_partial(responses, ev["id"], "ok")
    protocol.write_partial(responses, ev["id"], "boom")
    protocol.write_partial(responses, ev["id"], "later")

    sent: list[str] = []

    def flaky(event, body):
        if body == "boom":
            raise RuntimeError("platform down")
        sent.append(body)

    runtime.deliver_stream(inbox, responses, "tg", flaky)

    # delivered the first, choked on the second; the second and third
    # remain queued for the next loop (resumable, no skipped messages)
    assert sent == ["ok"]
    remaining = [protocol.read_partial(p)
                 for p in protocol.list_partials(responses, ev["id"])]
    assert remaining == ["boom", "later"]
    assert ev["_path"].exists()


def test_done_with_no_terminal_still_cleans_up(tmp_path):
    # A done event whose terminal response is missing (e.g. failure path
    # wrote none) must still be cleaned up after its partials drain, so
    # it doesn't wedge the gate loop forever.
    inbox, responses = tmp_path / "inbox", tmp_path / "responses"
    ev = _event(inbox, "tg", "task", "done")
    protocol.write_partial(responses, ev["id"], "only interim")

    sent, deliver = _capture()
    runtime.deliver_stream(inbox, responses, "tg", deliver)

    assert sent == ["only interim"]
    assert not ev["_path"].exists()
    assert not protocol.partials_dir(responses, ev["id"]).exists()


def test_pending_event_is_not_delivered(tmp_path):
    inbox, responses = tmp_path / "inbox", tmp_path / "responses"
    ev = _event(inbox, "tg", "task", "pending")
    protocol.write_partial(responses, ev["id"], "premature")

    sent, deliver = _capture()
    runtime.deliver_stream(inbox, responses, "tg", deliver)

    # pending events aren't active yet — nothing delivered, nothing lost
    assert sent == []
    assert len(protocol.list_partials(responses, ev["id"])) == 1
