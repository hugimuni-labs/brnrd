"""The board's parts (self threads/web-main design §D1, §D7): computed once in the loom, hashed per part,
served on loopback; one thread's move leaves the other parts alone, and a letter reaches the event stream fast."""

import gzip
import http.client
import json
import time

import pytest

from brr.loom.runtime.board import Parts, digest, serve, window
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter

from _step import write_thread


def readme(name):
    return f"---\nid: {name}\nstatus: open\ntense: plan\nopened-by: thread:inbox\n---\n# The {name} thread\n"


@pytest.fixture
def parts(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "web", readme("web"), "answer-fast", wait="0")
    write_thread(root, "docs", readme("docs"), "answer-fast", wait="0")
    home = Home(root)
    board = Parts(home, lambda: load_config(root))
    yield board
    board.stop.set()


def test_parts_are_hashed_stably_and_a_letter_moves_only_its_thread(parts):
    root = parts.home.root
    inject_letter(root, to="thread:web", body="first")
    inject_letter(root, to="thread:docs", body="other")
    assert parts.step()
    before = dict(parts.hashes)
    assert {"index", "beads", "thread/web", "thread/docs"} <= set(before)
    assert parts.parts["index"]["parts"] == {k: v for k, v in before.items() if k != "index"}
    assert all(digest(parts.parts[name]) == h for name, h in before.items())

    parts.revision = None  # the same ledger, recomputed: no part moves
    assert not parts.step()
    assert parts.hashes == before

    inject_letter(root, to="thread:web", body="second")
    assert parts.step()
    assert parts.hashes["thread/docs"] == before["thread/docs"]
    assert parts.hashes["thread/web"] != before["thread/web"]
    assert parts.hashes["index"] != before["index"]
    assert [r["body"] for r in parts.parts["thread/web"]["letters"]] == ["first", "second"]


def test_window_cuts_to_the_newest_and_reaches_back_to_a_target():
    part = {"id": "t", "letters": [{"id": f"l{i}"} for i in range(10)]}
    cut = window(part, most=3)
    assert [r["id"] for r in cut["letters"]] == ["l7", "l8", "l9"] and cut["more"]
    assert [r["id"] for r in window(part, before="l7", most=3)["letters"]] == ["l4", "l5", "l6"]
    assert [r["id"] for r in window(part, until="l2", most=3)["letters"]][0] == "l2"
    assert not window(part, most=20)["more"]


def get(port, path, **headers):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **headers})
    resp = conn.getresponse()
    body = resp.read()
    if resp.getheader("Content-Encoding") == "gzip":
        body = gzip.decompress(body)
    conn.close()
    return resp.status, resp.getheader("ETag"), body


def test_loopback_serves_parts_and_streams_a_letter_within_budget(parts):
    """The §D7 budget on a fixture home: first column ≤ 500 ms, a fact visible as an event ≤ 1 s."""
    import threading
    root = parts.home.root
    inject_letter(root, to="thread:web", body="hello")
    parts.step()
    server = serve(parts, 0)
    port = server.server_address[1]
    try:
        started = time.monotonic()
        status, etag, body = get(port, "/v1/loom/index", **{"Accept-Encoding": "gzip"})
        status_t, _, thread = get(port, "/v1/loom/thread/web")
        first_column = time.monotonic() - started
        assert status == status_t == 200
        assert json.loads(body)["parts"]["thread/web"] == parts.hashes["thread/web"]
        assert [r["body"] for r in json.loads(thread)["letters"]] == ["hello"]
        assert first_column <= 0.5, first_column
        assert get(port, "/v1/loom/index", **{"If-None-Match": etag})[0] == 304
        assert get(port, "/v1/loom/thread/nope")[0] == 404
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)  # DNS rebinding: a foreign Host
        conn.request("GET", "/v1/loom/index", headers={"Host": "evil.test"})
        assert conn.getresponse().status == 403

        runner = threading.Thread(target=parts.run, daemon=True)
        runner.start()
        stream = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        stream.request("GET", "/v1/loom/events", headers={"Host": f"127.0.0.1:{port}"})
        events = stream.getresponse()
        seen = parts.hashes["index"]
        assert events.fp.readline().startswith(b":")  # the padding past a proxy's buffer
        events.fp.readline()
        assert events.fp.readline().decode().strip() == "event: index"
        assert events.fp.readline().decode().strip() == f"data: {seen}"
        events.fp.readline()
        sent = time.monotonic()
        inject_letter(root, to="thread:web", body="again")
        assert events.fp.readline().decode().strip() == "event: index"
        moved = events.fp.readline().decode().strip().removeprefix("data: ")
        visible = time.monotonic() - sent
        assert moved != seen and visible <= 1.0, visible
        stream.close()
    finally:
        parts.stop.set()
        server.shutdown()
        server.server_close()
