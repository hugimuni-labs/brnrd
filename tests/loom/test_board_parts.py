"""The board's parts (self threads/web-main design §D1, §D7): computed once in the loom, hashed per part,
served on loopback; one thread's move leaves the other parts alone, and a letter reaches the event stream fast."""

import gzip
import http.client
import json
import time

import pytest

from brr.loom.runtime import board
from brr.loom.runtime.board import Parts, compute, digest, serve, window
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.daemon2.facts import Fact
from brr.loom.runtime.ledger import append, inject_letter

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


class Tape:
    """A stand-in for the rooms' tapes: the test says when they moved and what they hold."""

    def __init__(self):
        self.tools, self.live, self.moved = {}, {}, False

    def read(self):
        moved, self.moved = self.moved, False
        return moved


def test_a_tape_move_relays_only_its_thread_and_equals_a_whole_compute(parts, monkeypatch):
    """§D7's hot path: a tool line re-lays tools over the last parts, part for part what a whole compute gives."""
    root = parts.home.root
    inject_letter(root, to="thread:web", body="first")
    inject_letter(root, to="thread:docs", body="other")
    strand = "s-test-web001"
    append(parts.home, Fact(kind="lease", by="loom:test", data={
        "thread": "web", "strand": strand, "gen": 1, "shell": "fake", "core": "fake", "repos": []}))
    parts.tape = tape = Tape()
    assert parts.step()
    assert [s["id"] for t in parts.parts["index"]["threads"] if t["id"] == "web" for s in t["strands"]] == [strand]
    before = dict(parts.hashes)

    whole = []
    monkeypatch.setattr(board, "compute", lambda *a, **k: whole.append(1) or compute(*a, **k))
    tape.tools[strand] = [{"at": "2026-10-28T15:00:00Z", "end": None, "tool": "Bash", "arg": "zq9 probe"}]
    tape.live[strand] = {"open": [{"at": "2026-10-28T15:00:00Z", "tool": "Bash", "arg": "zq9 probe"}]}
    tape.moved = True
    assert parts.step()
    assert not whole  # the ledger stood still: no whole compute
    assert parts.hashes["thread/docs"] == before["thread/docs"]
    assert parts.hashes["thread/web"] != before["thread/web"]
    assert parts.parts["thread/web"]["tools"] == {strand: tape.tools[strand]}

    relaid = dict(parts.hashes)
    parts.revision = None  # the same inputs, recomputed whole
    assert not parts.step()
    assert whole and parts.hashes == relaid

    tape.moved = True  # past WHOLE seconds a tape move still goes out re-laid; the whole compute is the next tick's
    tape.tools[strand] = [*tape.tools[strand], {"at": "2026-10-28T15:00:01Z", "end": None, "tool": "Read",
                                                "arg": "zq9 two"}]  # a fresh list, as Tools.read writes
    monkeypatch.setattr(board, "WHOLE", 0.0)
    whole.clear()
    assert parts.step()
    assert not whole
    parts.step()
    assert whole
    whole.clear()
    assert not parts.step() and not whole  # due once: a still home stays still

    monkeypatch.setattr(board, "WHOLE", 3600.0)  # a ledger move with the tool line: the tool line goes out first
    inject_letter(root, to="thread:docs", body="moved with it")
    tape.moved = True
    tape.tools[strand] = [*tape.tools[strand], {"at": "2026-10-28T15:00:02Z", "end": None, "tool": "Bash",
                                                "arg": "zq9 three"}]
    assert parts.step() and not whole
    assert parts.parts["thread/web"]["tools"] == {strand: tape.tools[strand]}
    tape.moved = True  # a busy tape does not starve the due compute
    assert parts.step() and whole
    assert "moved with it" in json.dumps(parts.parts["thread/docs"])


def test_a_wish_ships_in_its_thread_part_and_the_index_keeps_counts(parts):
    root = parts.home.root
    letter = inject_letter(root, to="thread:web", body="make it fast")
    inject_letter(root, to="thread:docs", body="other")
    assert parts.step()
    before = dict(parts.hashes)
    append(parts.home, Fact(kind="wish", by="person:ada", id="w-fast", data={
        "wish": "w-fast", "says": [[letter, 0, None]], "from": "person", "line": "make it fast", "thread": "web"}))
    assert parts.step()
    index = parts.parts["index"]
    assert parts.hashes["thread/docs"] == before["thread/docs"]
    assert [w["wish"] for w in parts.parts["thread/web"]["wishes"]] == ["w-fast"]
    assert sum(index["wishes"]["web"].values()) == 1 and "docs" not in index["wishes"]
    assert "w-fast" not in json.dumps(index)  # the index carries no wish rows: a moved wish resends one thread
    assert index["faces"]["running"][0] == index["faces"]["idle"][0] == "brnrd"  # the rail's faces, at rest on the name
    assert len(index["faces"]["running"]) > 1


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


def post(port, payload, host=None, **headers):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/v1/loom/letters", body=json.dumps(payload).encode(),
                 headers={"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json", **headers})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def test_a_board_letter_lands_as_the_persons_own_and_foreign_pages_cannot_send(parts):
    """POST /v1/loom/letters drops where the channel's letters land; the intake reads it as the person's."""
    from brr.loom.runtime.connector import drain
    from brr.loom.runtime.ledger import read_facts
    root = parts.home.root
    (root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    (root / "self/people/ada").mkdir(parents=True)
    (root / "self/people/ada/channels.toml").write_text('channels = ["mine:42"]\n')
    parts.step()
    # The header's "as ada via channel:mine/42": who a letter typed here goes out as.
    assert (parts.parts["index"]["person"], parts.parts["index"]["via"]) == ("ada", "channel:mine/42")
    server = serve(parts, 0)
    port = server.server_address[1]
    try:
        assert post(port, {"body": "x"}, Origin="https://evil.test")[0] == 403
        assert post(port, {"body": "x"}, host="evil.test")[0] == 403
        assert post(port, {"body": "x"}, **{"Content-Type": "text/plain"})[0] == 415
        assert post(port, {"body": "  "})[0] == 400
        assert post(port, {"body": "x", "wish": "nope"})[0] == 400
        assert not list(root.glob("loom/connectors/mine/state/in/*"))

        status, body = post(port, {"body": "from the board", "typed_by": "s-ae66-abcdef"},
                            Origin=f"http://127.0.0.1:{port}")
        assert status == 200, body
        sent = json.loads(body)
        drop = root / "loom/connectors/mine/state/in"
        drain(parts.home, "mine", drop, {})
        letter = next(f for f in read_facts(parts.home) if f.id == sent["id"])
        assert letter.data["body"] == "from the board" and letter.data["from"] == "person:ada"
        assert parts.step()
        typed = [r for p, part in parts.parts.items() if p.startswith("thread/") for r in part["letters"]
                 if r["id"] == sent["id"]]
        assert [r["typed_by"] for r in typed] == ["s-ae66-abcdef"]
    finally:
        parts.stop.set()
        server.shutdown()
        server.server_close()


def test_loopback_serves_the_spa_build_beside_the_parts(parts, tmp_path):
    """The loom serves the SvelteKit build itself: ``/`` is the prerendered page whose probe finds the board, a client
    route gets the 200.html shell, a hashed chunk is immutable, a missing chunk 404s, nothing leaves the build."""
    build = tmp_path / "build"
    (build / "_app" / "immutable").mkdir(parents=True)
    (build / "index.html").write_text("<p>root</p>")
    (build / "200.html").write_text("<p>shell</p>")
    (build / "pricing.html").write_text("<p>pricing</p>")
    (build / "_app" / "version.json").write_text('{"version":"1"}')
    (build / "_app" / "immutable" / "a.js").write_text("export const a = 1;" * 400)
    (tmp_path / "secret").write_text("no")
    parts.step()
    server = serve(parts, 0, lambda: build)
    port = server.server_address[1]

    def fetch(path, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **headers})
        resp = conn.getresponse()
        body = resp.read()
        if resp.getheader("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        conn.close()
        return resp.status, resp.getheader("Content-Type") or "", resp.getheader("Cache-Control") or "", body

    try:
        assert fetch("/") == (200, "text/html; charset=utf-8", "no-cache", b"<p>root</p>")
        assert fetch("/dashboard/runs?x=1")[3] == b"<p>shell</p>"  # a client route: the router owns it
        assert fetch("/pricing")[3] == b"<p>pricing</p>"
        status, kind, cache, body = fetch("/_app/immutable/a.js", **{"Accept-Encoding": "gzip"})
        assert (status, kind, cache) == (200, "text/javascript; charset=utf-8", "public, max-age=31536000, immutable")
        assert body.startswith(b"export const a")
        assert fetch("/_app/version.json")[:3] == (200, "application/json", "no-cache")
        assert fetch("/_app/immutable/gone.js")[0] == 404  # never the shell: SvelteKit's stale-build recovery
        assert fetch("/../secret")[3] != b"no" and fetch("/%2e%2e/secret")[3] != b"no"
        assert fetch("/v1/nope")[0] == 404  # an API path is never the shell
        assert json.loads(fetch("/v1/loom/index")[3])["parts"]  # the parts still answer beside it
    finally:
        parts.stop.set()
        server.shutdown()
        server.server_close()

    nothing = serve(parts, 0, lambda: None)  # no `npm run build` yet: a plain 404, the parts unaffected
    try:
        conn = http.client.HTTPConnection("127.0.0.1", nothing.server_address[1], timeout=5)
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{nothing.server_address[1]}"})
        assert conn.getresponse().status == 404
    finally:
        nothing.shutdown()
        nothing.server_close()
