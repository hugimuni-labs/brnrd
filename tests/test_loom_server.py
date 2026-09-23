"""The loom listener (``brr.loom.server``) over a real socket on an ephemeral port."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from pathlib import Path

import pytest

from brr.loom import server


@pytest.fixture
def served(tmp_path: Path):
    static = tmp_path / "static"
    (static / "js").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>loom</title>", encoding="utf-8")
    (static / "js" / "app.js").write_text("console.log('beat')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    home = tmp_path / "home"
    fold = home / "bench" / "acme__widgets" / "src" / "a.py" / "abc1234.md"
    fold.parent.mkdir(parents=True)
    fold.write_text("---\nplace: src/a.py\n---\nthe fold\n", encoding="utf-8")
    (home / "shuttle.json").write_text("{}", encoding="utf-8")
    (home / "private.md").write_text("outside the bench", encoding="utf-8")
    calls = []

    def build():
        calls.append(1)
        return {"at": "now", "beat_ms": 600, "n": len(calls)}

    listener = server.make_server(tmp_path / "repo", home, port=0, static_dir=static, build=build, beat_ms=60_000)
    thread = threading.Thread(target=listener.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield {"server": listener, "calls": calls}
    listener.shutdown()
    listener.server_close()
    thread.join(timeout=5)


def get(listener, path: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", listener.port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def raw_get(listener, target: str) -> bytes:
    """A request line sent byte for byte — no client normalises the path."""
    with socket.create_connection(("127.0.0.1", listener.port), timeout=5) as sock:
        sock.sendall(f"GET {target} HTTP/1.0\r\nHost: 127.0.0.1:{listener.port}\r\n\r\n".encode())
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def test_binds_loopback_only(served):
    assert served["server"].server_address[0] == "127.0.0.1"
    assert served["server"].url.startswith("http://127.0.0.1:")


def test_loom_serves_the_page(served):
    for path in ("/loom", "/loom/"):
        status, headers, body = get(served["server"], path)
        assert status == 200 and body.startswith(b"<!doctype html>")
        assert headers["Content-Type"].startswith("text/html")
    status, headers, body = get(served["server"], "/loom/js/app.js")
    assert status == 200 and body == b"console.log('beat')"


def test_state_json_is_cached_for_the_beat(served):
    status, headers, body = get(served["server"], "/loom/state.json")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert json.loads(body) == {"at": "now", "beat_ms": 600, "n": 1}
    assert json.loads(get(served["server"], "/loom/state.json")[2])["n"] == 1
    assert len(served["calls"]) == 1


def test_bench_serves_a_fold_inside_the_bench_only(served):
    status, headers, body = get(served["server"], "/loom/bench?path=acme__widgets/src/a.py/abc1234")
    assert status == 200 and b"the fold" in body and headers["Content-Type"].startswith("text/markdown")
    for bad in ("../private", "..%2Fprivate", str(home_of(served) / "private.md"), "acme__widgets/../../private", ""):
        assert get(served["server"], f"/loom/bench?path={bad}")[0] == 404, bad


def home_of(served) -> Path:
    return served["server"].account_home


def test_path_traversal_is_refused(served):
    for target in ("/loom/../secret.txt", "/loom/%2e%2e/secret.txt", "/loom/js/../../secret.txt", "/loom/..%2fsecret.txt"):
        response = raw_get(served["server"], target)
        assert response.startswith(b"HTTP/1.0 404"), (target, response[:40])
        assert b"outside" not in response


def test_symlink_out_of_static_is_refused(served, tmp_path):
    (served["server"].static_dir / "leak.txt").symlink_to(tmp_path / "secret.txt")
    assert get(served["server"], "/loom/leak.txt")[0] == 404


def test_foreign_host_header_is_refused(served):
    assert get(served["server"], "/loom/state.json", {"Host": "evil.example"})[0] == 403
    assert get(served["server"], "/loom/state.json", {"Host": f"localhost:{served['server'].port}"})[0] == 200


def test_events_emit_a_state_frame(served):
    with socket.create_connection(("127.0.0.1", served["server"].port), timeout=5) as sock:
        sock.sendall(f"GET /loom/events HTTP/1.0\r\nHost: 127.0.0.1:{served['server'].port}\r\n\r\n".encode())
        data = b""
        while b"\n\n" not in data.split(b"\r\n\r\n", 1)[-1] or b"\r\n\r\n" not in data:
            chunk = sock.recv(65536)
            assert chunk, "stream closed before a frame"
            data += chunk
    head, stream = data.split(b"\r\n\r\n", 1)
    assert b"text/event-stream" in head
    frame = stream.split(b"\n\n", 1)[0].decode()
    event, payload = frame.split("\n", 1)
    assert event == "event: state"
    assert json.loads(payload[len("data: "):])["beat_ms"] == 600


def test_state_cache_rebuilds_after_a_beat():
    clock = [0.0]
    cache = server.StateCache(lambda: {"at": clock[0]}, beat_ms=600, clock=lambda: clock[0])
    first = cache.get()
    assert json.loads(first) == {"at": 0.0} and cache.builds == 1
    clock[0] = 0.5
    assert cache.get() is first and cache.builds == 1
    clock[0] = 0.6
    cache.get()
    assert cache.builds == 2


def test_unknown_route_is_404(served):
    assert get(served["server"], "/")[0] == 404
    assert get(served["server"], "/loom/nope.css")[0] == 404


def test_tree_json_is_the_whole_tracked_tree_or_honestly_empty(served, tmp_path: Path):
    # the served fixture's repo root is not a git checkout: the tree is empty, never an error
    status, headers, body = get(served["server"], "/loom/tree.json")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("application/json")
    tree = json.loads(body)
    assert tree["repo"]["files"] == []
    assert tree["home"]["files"] == []
    assert tree["captured_at"].endswith("Z")
