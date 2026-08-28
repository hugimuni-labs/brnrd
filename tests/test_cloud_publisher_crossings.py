"""The crossing tail — a stream, published as one.

`edge` is a **cursor**: whichever boundary is current at publish time. A
client polling on an interval sees whichever edge that poll caught, so two
injections inside one window means one was never published at all. Measured
2026-08-28: `/ascii` polls every 2s, `roomPager.recordPages` dedupes on
`edge.at`, and one wake crossed 119 boundaries in ~75 minutes, bursty — so
its "messages read" count was counting polls that landed, not crossings.

A cursor cannot be sampled into a stream. These pin the stream.
"""

import json

from brr.gates.cloud_publisher import (
    _CROSSINGS_MAX,
    _crossings_payload,
    _edge_payload,
)


def _write(tmp_path, run_id, records):
    d = tmp_path / "runs" / run_id
    d.mkdir(parents=True)
    (d / "boundaries.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    return tmp_path


def _b(at, *, inject=False, act="orient", subagent=False, detail="ls"):
    row = {"at": at, "phase": "post", "act": act, "detail": detail, "cwd": "."}
    if inject:
        row["inject"] = "a letter arrived"
    if subagent:
        row["subagent"] = True
    return row


def test_a_burst_publishes_every_crossing_not_only_the_last(tmp_path):
    # Three injections and a quiet boundary on top. `edge` sees only the
    # quiet one — which is exactly how a crossing went missing.
    brr = _write(
        tmp_path,
        "r1",
        [
            _b("10:00:00Z", inject=True),
            _b("10:00:01Z", inject=True),
            _b("10:00:02Z", inject=True),
            _b("10:00:03Z"),
        ],
    )
    edge = _edge_payload(brr, "r1")
    assert edge["at"] == "10:00:03Z"
    assert edge["injected"] is False, "the cursor sits on the quiet boundary"

    crossings = _crossings_payload(brr, "r1")
    assert [c["at"] for c in crossings] == ["10:00:02Z", "10:00:01Z", "10:00:00Z"]
    assert all(c["injected"] for c in crossings)


def test_no_crossings_is_a_real_answer(tmp_path):
    brr = _write(tmp_path, "r1", [_b("10:00:00Z"), _b("10:00:01Z")])
    assert _crossings_payload(brr, "r1") == []


def test_the_tail_is_bounded(tmp_path):
    brr = _write(
        tmp_path, "r1", [_b(f"10:00:{i:02d}Z", inject=True) for i in range(20)]
    )
    crossings = _crossings_payload(brr, "r1")
    assert len(crossings) == _CROSSINGS_MAX
    # Newest first: a bounded tail keeps the near end, never the far one.
    assert crossings[0]["at"] == "10:00:19Z"


def test_a_limbs_crossing_is_not_the_runs(tmp_path):
    # #1095: a subagent's boundary is not the run's, and that has to hold for
    # the tail exactly as it held for the cursor — one reader, one rule.
    brr = _write(
        tmp_path,
        "r1",
        [_b("10:00:00Z", inject=True, subagent=True), _b("10:00:01Z", inject=True)],
    )
    assert [c["at"] for c in _crossings_payload(brr, "r1")] == ["10:00:01Z"]


def test_a_crossing_carries_the_same_bounded_detail_as_the_edge(tmp_path):
    # One projection for both, so a bound applied to one is applied to both.
    # Without that, the disclosure fix would have covered the cursor and left
    # the stream open — the same shape as bounding one renderer of four.
    payload = "cat > /tmp/reply.md <<'EOF' secret prose that must not travel"
    brr = _write(tmp_path, "r1", [_b("10:00:00Z", inject=True, detail=payload)])
    crossing = _crossings_payload(brr, "r1")[0]
    assert "secret prose" not in crossing["detail"]
    assert crossing["detail"] == _edge_payload(brr, "r1")["detail"]


def test_an_unreadable_transcript_yields_nothing_rather_than_a_zero_row(tmp_path):
    assert _crossings_payload(tmp_path, "nope") == []
    assert _edge_payload(tmp_path, "nope") is None
