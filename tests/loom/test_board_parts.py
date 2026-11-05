"""The board's parts (self threads/web-main design §D1, §D7): computed once in the loom, hashed per part,
served on loopback; one thread's move leaves the other parts alone, and a letter reaches the event stream fast."""

import gzip
import http.client
import json
import time
from pathlib import Path

import pytest

from brr.loom.runtime.board import Parts, Tools, digest, serve, window
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


def test_the_looms_own_heartbeat_recomputes_nothing(parts, monkeypatch):
    from brr.loom.runtime import board
    root = parts.home.root
    inject_letter(root, to="thread:web", body="first")
    assert parts.step()
    computed = []
    real = board.compute
    monkeypatch.setattr(board, "compute", lambda *a: computed.append(1) or real(*a))
    append(parts.home, Fact(kind="router.renewed", by="loom", id="router.renewed:test:1:1",
                            data={"install": "test", "gen": 1, "until": time.time() + 60}))
    assert not parts.step()
    assert computed == []
    inject_letter(root, to="thread:web", body="second")
    assert parts.step()
    assert computed == [1]


def test_a_tape_move_recuts_only_its_thread_paced_and_never_recomputes(parts, monkeypatch):
    """busy-cpu: a body's tool calls move its tape several times a second; each re-cuts its thread's tapes over the
    last compute at most once per TAPE s, and the result is the part a whole compute would give."""
    from brr.loom.runtime import board, tooltrace
    root = parts.home.root
    append(parts.home, Fact(kind="router", by="loom:aaaa", data={"gen": 1}))
    append(parts.home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": "web", "strand": "s-aaaa-webbod", "gen": 1, "install": "aaaa", "router_gen": 1,
        "shell": "fake", "core": "fake", "repos": []}))
    inject_letter(root, to="thread:docs", body="other")
    assert parts.step()
    before = dict(parts.hashes)
    computed = []
    real = board.compute
    monkeypatch.setattr(board, "compute", lambda *a: computed.append(1) or real(*a))
    clock = [time.monotonic()]
    monkeypatch.setattr(board.time, "monotonic", lambda: clock[0])

    (parts.home.room("s-aaaa-webbod") / "port").mkdir(parents=True)

    def call(n):
        tooltrace.note(parts.home.room("s-aaaa-webbod"), json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": f"echo {n}"}, "tool_use_id": f"t{n}"}), "pre")
    call(1)
    parts.tape.listed = None  # the room is listed at the next read, as within Tools.EVERY
    assert not parts.step()  # within TAPE of the compute: held, not lost
    call(2)
    clock[0] += board.TAPE
    assert parts.step()
    assert computed == []
    assert [c["cmd"] for c in parts.parts["thread/web"]["tools"]["s-aaaa-webbod"]] == ["echo 1", "echo 2"]
    assert parts.hashes["thread/docs"] == before["thread/docs"] and parts.hashes["beads"] == before["beads"]
    assert parts.hashes["thread/web"] != before["thread/web"]
    assert parts.parts["index"]["parts"] == {k: v for k, v in parts.hashes.items() if k != "index"}
    assert not parts.step()  # nothing moved since
    recut = dict(parts.hashes)
    parts.revision = None  # a whole compute over the same ledger and tapes gives the same parts
    assert not parts.step()
    assert parts.hashes == recut and computed == [1]


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
    # the index carries no wish rows (beads_delta may name the id as a bead patch)
    assert "w-fast" not in json.dumps({k: v for k, v in index.items() if k not in {"beads_delta", "beads_deltas"}})
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


def test_a_quiet_room_is_stat_ed_at_each_listing_and_a_moving_one_at_every_read(tmp_path):
    """Hundreds of dead rooms cost the board's 0.25 s read nothing between listings."""
    def tape(strand, text=""):
        port = tmp_path / "rooms" / strand / "port"
        port.mkdir(parents=True, exist_ok=True)
        with open(port / "tools.jsonl", "a") as out:
            out.write(text)
    tape("s-aa-moving")
    tools = Tools(Home(tmp_path), every=60)
    assert tools.read() and set(tools.tools) == {"s-aa-moving"}
    tape("s-aa-woken")
    assert not tools.read()  # not listed yet
    tape("s-aa-moving", "\n")
    assert tools.read()  # moved since the listing: still read at every tick
    tools.listed -= 61
    assert tools.read() and set(tools.tools) == {"s-aa-moving", "s-aa-woken"}


def test_two_rooms_moving_in_one_read_survive_the_next_listing(tmp_path):
    """Each moving room's hot mark is the read's clock, so the next listing can age it (#2569 stored a live dict)."""
    for strand in ("s-aa-one", "s-aa-two"):
        port = tmp_path / "rooms" / strand / "port"
        port.mkdir(parents=True)
        (port / "tools.jsonl").write_text("")
    tools = Tools(Home(tmp_path), every=60)
    assert tools.read() and set(tools.tools) == {"s-aa-one", "s-aa-two"}
    assert all(isinstance(at, float) for at in tools.hot.values())
    tools.listed -= 61
    tools.read()
    assert set(tools.tools) == {"s-aa-one", "s-aa-two"}


def test_a_repeat_verdict_on_the_same_wish_is_the_same_letter(parts):
    """10099: a slow agree pressed three times landed three verdict letters; a repeat answers the first one's id."""
    root = parts.home.root
    (root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    (root / "self/people/ada").mkdir(parents=True)
    (root / "self/people/ada/channels.toml").write_text('channels = ["mine:42"]\n')
    first = parts.send("agree &w-abc123", wish="w-abc123", verdict="agree")
    assert [parts.send("agree &w-abc123", wish="w-abc123", verdict="agree") for _ in range(2)] == [first] * 2
    assert len(list(root.glob("loom/connectors/mine/state/in/*"))) == 1
    # another verdict, or another wish, is a letter of its own
    assert parts.send("Not quite on &w-abc123", wish="w-abc123", verdict="correct") != first
    assert parts.send("agree &w-def456", wish="w-def456", verdict="agree") != first
    assert len(list(root.glob("loom/connectors/mine/state/in/*"))) == 3


def test_a_thread_part_ships_the_wish_chain_and_its_person_letter(parts):
    """Each thread part carries the ancestors of its wishes (and of its letters) up to the person root."""
    root = parts.home.root
    write_thread(root, "inbox", readme("inbox"), "answer-fast", wait="0")
    ask = inject_letter(root, to="thread:inbox", body="please make web fast")
    elsewhere = inject_letter(root, to="thread:docs", body="from docs, also about web")
    append(parts.home, Fact(kind="wish", by="person:ada", id="w-fast", data={
        "wish": "w-fast", "says": [[ask, 0, None]], "from": "person", "line": "make web fast",
        "thread": "web"}))
    inject_letter(root, to="thread:web", body="on it", re=elsewhere)
    assert parts.step()
    chain = parts.parts["thread/web"]["chain"]
    assert ask in chain and chain[ask][1] == "person"
    assert chain[ask][4].startswith("please make web fast")
    assert "w-fast" in chain and chain["w-fast"][0] == ask
    assert elsewhere in chain and chain[elsewhere][1] == "person"
    assert len(json.dumps(chain)) < 8_000  # a few KB, not the whole beads part
    assert ask not in {r["id"] for r in parts.parts["thread/web"]["letters"]}
    assert "chain" not in parts.parts["thread/inbox"]


def test_beads_since_returns_a_delta_and_a_new_letter_does_not_resend_the_part(parts):
    """A letter moves beads; GET ?since=<old hash> is the patch, not the 1 MB part."""
    root = parts.home.root
    inject_letter(root, to="thread:web", body="first")
    assert parts.step()
    before = parts.hashes["beads"]
    n = len(parts.parts["beads"])
    inject_letter(root, to="thread:web", body="second")
    assert parts.step()
    delta = parts.bead_deltas[before]
    assert delta["since"] == before and delta["hash"] == parts.hashes["beads"]
    assert delta["drop"] == []
    assert 1 <= len(delta["set"]) < n
    assert parts.parts["index"]["beads_delta"]["since"] == before
    server = serve(parts, 0)
    port = server.server_address[1]
    try:
        status, _, body = get(port, f"/v1/loom/beads?since={before}")
        got = json.loads(body)
        assert status == 200 and got["set"] == delta["set"] and got["drop"] == []
        _, _, full = get(port, "/v1/loom/beads")
        assert len(body) < len(full)
        empty = json.loads(get(port, f"/v1/loom/beads?since={parts.hashes['beads']}")[2])
        assert empty == {"since": parts.hashes["beads"], "hash": parts.hashes["beads"], "set": {}, "drop": []}
    finally:
        parts.stop.set()
        server.shutdown()
        server.server_close()


def test_inbox_has_no_chain_and_setup_flow_stays_small(parts):
    """The strip needs wish roots and crossing letters, not every letter of a 200-letter part; inbox has no strip."""
    root = parts.home.root
    write_thread(root, "inbox", readme("inbox"), "answer-fast", wait="0")
    write_thread(root, "setup-flow", readme("setup-flow"), "answer-fast", wait="0")
    ask = inject_letter(root, to="thread:inbox", body="please set it up")
    append(parts.home, Fact(kind="wish", by="person:ada", id="w-setup", data={
        "wish": "w-setup", "says": [[ask, 0, None]], "from": "person",
        "line": "set it up", "thread": "setup-flow"}))
    prev = ask
    for i in range(80):
        prev = inject_letter(root, to="thread:setup-flow", body=f"step {i} of the setup", re=prev)
    assert parts.step()
    assert "chain" not in parts.parts["thread/inbox"]
    chain = parts.parts["thread/setup-flow"]["chain"]
    assert ask in chain and "w-setup" in chain
    assert len(json.dumps(chain)) < 8_000


def test_beads_since_composes_two_letters_to_the_current_hash(parts):
    """Two letters between two steps: ?since=<first hash> returns both rows with hash=current."""
    root = parts.home.root
    inject_letter(root, to="thread:web", body="first")
    assert parts.step()
    h1 = parts.hashes["beads"]
    second = inject_letter(root, to="thread:web", body="second")
    assert parts.step()
    third = inject_letter(root, to="thread:web", body="third")
    assert parts.step()
    current = parts.hashes["beads"]
    server = serve(parts, 0)
    port = server.server_address[1]
    try:
        status, _, body = get(port, f"/v1/loom/beads?since={h1}")
        got = json.loads(body)
        assert status == 200 and got["hash"] == current and got["since"] == h1
        assert second in got["set"] and third in got["set"]
    finally:
        parts.stop.set()
        server.shutdown()
        server.server_close()


def test_topic_newest_matches_the_seed_wake():
    """board.topic_newest is a copy of the seed wake's; a drift in either fails."""
    import importlib.machinery
    import importlib.util
    from brr.loom.runtime.board import topic_newest as board_newest
    wake = Path(__file__).resolve().parents[2] / "src/brr/loom/runtime/seed/core/loom/wake"
    loader = importlib.machinery.SourceFileLoader("seed_wake", str(wake))
    spec = importlib.util.spec_from_loader("seed_wake", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    page = (
        "# Board: how wishes and threads are drawn\n\n"
        "### 2026-10-24 · Older entry · [x](y)\n\nbody\n\n"
        "### 2026-10-25 · A thread shows the face its strand chose · [faces](x)\n\n"
        "Chose four states.\n"
    )
    assert board_newest(page) == mod.topic_newest(page) == (
        "2026-10-25", "A thread shows the face its strand chose")
    assert board_newest("# no entries\n") == mod.topic_newest("# no entries\n") == ("", "")


def test_topics_is_its_own_part_not_on_the_index(parts):
    root = parts.home.root
    page = root / "self/topics/board.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "# Board: how wishes and threads are drawn\n\n"
        "### 2026-10-25 · A thread shows the face its strand chose · [faces](x)\n\n"
        "Chose four states, in order.\n"
    )
    cited = inject_letter(root, to="thread:web", body="see topics/board for the column order")
    assert parts.step()
    index = parts.parts["index"]
    assert "topics" in index["parts"]
    assert "topics" not in {k for k in index if k != "parts"}
    rows = parts.parts["topics"]
    assert rows[0]["id"] == "board"
    assert rows[0]["at"] == "2026-10-25"
    assert rows[0]["line"].startswith("A thread shows the face")
    assert cited in rows[0]["cites"]
    assert "Chose four states" in rows[0]["quote"]
    before = parts.hashes["topics"]
    inject_letter(root, to="thread:web", body="unrelated")
    assert parts.step()
    assert parts.hashes["topics"] == before
    server = serve(parts, 0)
    port = server.server_address[1]
    try:
        status, _, body = get(port, "/v1/loom/topics")
        assert status == 200 and json.loads(body)[0]["id"] == "board"
        idx = json.loads(get(port, "/v1/loom/index")[2])
        assert "topics" not in idx
        assert idx["parts"]["topics"] == parts.hashes["topics"]
    finally:
        parts.stop.set()
        server.shutdown()
        server.server_close()
