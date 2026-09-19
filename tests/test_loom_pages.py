"""The bench pages (``brr.loom.pages``) — one fixture per kind, and the route."""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
from pathlib import Path

import pytest

from brr.loom import pages, server, state
from tests.test_loom_state import CHILD, NOW, OLD, RUN, _jsonl, _write, iso, machine  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _strict(monkeypatch):
    monkeypatch.setenv("BRNRD_LOOM_STRICT", "1")


@pytest.fixture
def kid_contract(machine):
    brr = machine["brr"]
    run_md = brr / "runs" / CHILD / "run.md"
    run_md.write_text(run_md.read_text().replace(
        "title: the kid", "title: the kid\nspawn_contract_branch: brr/kid\n"
        f"spawn_contract_report: {machine['repo'] / 'kid-report.md'}",
    ))
    _write(machine["repo"] / "kid-report.md", "# report\n")
    _write(brr / "inbox" / f"evt-{CHILD}.md", "---\nid: evt-x\nsource: spawn\nstatus: processing\n---\n" + "spec " * 200)
    _write(brr / "forge-pr-state.json", json.dumps({"schema": 3, "prs": [
        {"number": 12, "branch": "brr/kid", "state": "OPEN", "url": "https://forge/pr/12"},
        {"number": 99, "branch": "brr/other", "state": "OPEN", "url": "https://forge/pr/99"},
    ]}))
    return machine


def test_bead_page_is_one_row_whole(machine):
    page, read = pages.bead_page(machine["repo"], machine["home"], RUN, 1)
    assert page["n"] == 1 and page["act"] == "mutate" and page["prev"] == 0 and page["next"] == 2
    assert page["detail_full"] == "write the post" and page["ctx_after"] == 272_900 and page["delta"] == 300
    assert page["place_kind"] == "wire"  # the row wrote the outbox's .card
    assert page["window_tokens"] is None  # the HUD names no window size; read says where it looked
    assert any(r.endswith("portal-state.json") for r in read) and any(r.endswith("boundaries.jsonl") for r in read)
    first, _ = pages.bead_page(machine["repo"], machine["home"], RUN, 0)
    assert first["chip"] == "⌁[b·o·d]: ⏱ 10m │ spend 2.2m" and len(first["detail_full"]) > 160
    last, _ = pages.bead_page(machine["repo"], machine["home"], RUN, -1)
    assert last["act"] == "orient" and last["next"] is None
    assert pages.bead_page(machine["repo"], machine["home"], RUN, 99)[0] is None
    assert pages.bead_page(machine["repo"], machine["home"], "../etc", 0) == (None, [])


def test_bead_n_in_state_addresses_the_page(machine):
    beads = state.build(machine["repo"], machine["home"], now=NOW)["beads"]
    for bead in beads:
        page, _ = pages.bead_page(machine["repo"], machine["home"], RUN, bead["n"])
        assert page["at"] == bead["at"] and page["act"] == bead["act"]


def test_pass_page_joins_contract_card_produce_and_beads(kid_contract):
    m = kid_contract
    page, read = pages.pass_page(m["repo"], m["home"], CHILD)
    assert page["contract"].startswith("spec spec") and len(page["contract"]) == 600
    assert (page["shell"], page["core"], page["parent"], page["status"]) == ("codex", "astra", RUN, "running")
    assert page["ended"] is None and page["duration_s"] is None and page["branch"] == "brr/kid"
    assert page["produce"]["prs"] == [{"number": 12, "url": "https://forge/pr/12", "state": "OPEN"}]
    # no git history here: the relics' commits stand in, and read names the git call tried
    assert page["produce"]["commits"] == [{"sha": "abc1234", "subject": ""}]
    assert any(r.startswith("$ log") for r in read)
    assert page["report_exists"] is True
    assert len(page["beads"]) == 12 and page["beads"][-1]["n"] == 29

    live, _ = pages.pass_page(m["repo"], m["home"], RUN)
    assert live["card"]["now"] == "weaving the feed" and live["mood"] == "curious"
    assert [s["id"] for s in live["strands"]][0] == CHILD

    old, _ = pages.pass_page(m["repo"], m["home"], OLD)
    assert old["status"] == "done" and old["ended"] == iso(NOW - 18000) and old["duration_s"] == 2000
    assert old["produce"]["pages"] == ["p.md"] and {p["number"] for p in old["produce"]["prs"]} == {7, 9}
    assert old["topics"] == ["the-loom", "the-post"]
    assert pages.pass_page(m["repo"], m["home"], "run-nope")[0] is None


def test_pass_page_reads_the_branch_log(kid_contract, tmp_path):
    m = kid_contract
    host = m["repo"]
    git = ["git", "-C", str(host), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "base"],
                 ["checkout", "-q", "-b", "brr/kid"], ["commit", "-q", "--allow-empty", "-m", "the kid's work"]):
        subprocess.run(git + args, check=True, env=env)
    page, _ = pages.pass_page(host, m["home"], CHILD)
    assert [c["subject"] for c in page["produce"]["commits"]] == ["the kid's work"]


def test_item_page_parses_the_file_with_siblings(machine):
    page, read = pages.item_page(machine["home"], "w-3")
    assert page["state"] == "held" and page["taken"] == "run-260922-0930-bbbb" and page["needs"] == ["w-4"]
    assert page["siblings"] == [{"id": "w-4", "title": "Four", "state": "ready"}]
    assert read[0].endswith("surface/warp/w-3.md")
    two, _ = pages.item_page(machine["home"], "w-2")
    assert two["siblings"] == [{"id": "w-1", "title": "One", "state": "done"}] and two["type"] == "decision"
    assert pages.item_page(machine["home"], "w-404")[0] is None
    assert pages.item_page(machine["home"], "../w-1") == (None, [])


def test_place_page_joins_heat_beads_passes_and_folds(machine):
    snapshot = state.build(machine["repo"], machine["home"], now=NOW)
    page, _ = pages.place_page(machine["repo"], machine["home"], "src/brr/hud.py", snapshot)
    assert page["kind"] == "file" and page["tree"] == "repo" and page["heat"] > 0.95 and page["topics"] == ["the-loom"]
    assert [b["run"] for b in page["beads"]] == [RUN, OLD]  # the live run's bead first, then the old pass's
    assert [p["run"] for p in page["passes"]] == [RUN, OLD]  # newest touch first
    assert page["passes"][1] == {"run": OLD, "name": "the old run", "last": iso(NOW - 5 * 3600), "mutated": False}
    home, _ = pages.place_page(machine["repo"], machine["home"], "knowledge/repos/acme/design.md", snapshot)
    assert home["kind"] == "home" and home["tree"] == "home" and len(home["beads"]) == 1
    fold, _ = pages.place_page(machine["repo"], machine["home"], "src/brr/daemon.py", snapshot)
    assert fold["heat"] is None and fold["fold"]["marks"] == ["keep", "drop"] and "body" in fold["fold"]["text"]
    assert pages.place_page(machine["repo"], machine["home"], "src/nowhere.py", snapshot)[0] is None
    assert pages.place_page(machine["repo"], machine["home"], "../secret", snapshot) == (None, [])


def test_heddle_page_renders_the_topic_and_its_index(machine):
    snapshot = state.build(machine["repo"], machine["home"], now=NOW)
    page, read = pages.heddle_page(machine["home"], "the-post", snapshot)
    assert page["rune"] == "ᛈ" and page["signature"]["places"] == ["media/**"]
    assert page["counts"] == {"strand": 1, "message": 1} and page["total"] == 2
    assert page["rows"][-1] == {"at": iso(NOW - 50), "kind": "message", "ref": "m2", "run": RUN}
    assert pages.heddle_page(machine["home"], "the-nothing")[0] is None


def test_page_routes_over_the_socket(kid_contract):
    m = kid_contract
    listener = server.make_server(m["repo"], m["home"], port=0, beat_ms=60_000)
    thread = threading.Thread(target=listener.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", listener.port, timeout=10)
            conn.request("GET", path)
            resp = conn.getresponse()
            body = json.loads(resp.read())
            conn.close()
            return resp.status, body

        status, body = get("/loom/page/item?id=w-3")
        assert status == 200 and body["state"] == "held" and body["read"]
        assert get(f"/loom/page/bead?run={RUN}&n=0")[1]["act"] == "probe"
        assert get(f"/loom/page/pass?id={CHILD}")[1]["branch"] == "brr/kid"
        assert get("/loom/page/place?path=src/brr/hud.py")[0] == 200
        assert get("/loom/page/heddle?slug=the-loom")[1]["slug"] == "the-loom"
        status, body = get("/loom/page/item?id=w-404")
        assert status == 404 and body["error"] == "no such item" and body["read"]
        assert get(f"/loom/page/bead?run={RUN}&n=x")[0] == 400
        assert get("/loom/page/nope")[0] == 404
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=5)


# ── the place page grows: gh_url, text, attention ─────────────────────────


def _fake_origin(repo: Path, url: str, head: str | None = "trunk") -> None:
    _write(repo / ".git" / "config", f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    if head:
        _write(repo / ".git" / "refs" / "remotes" / "origin" / "HEAD", f"ref: refs/remotes/origin/{head}\n")


def test_place_page_links_github_and_serves_text(machine):
    repo = machine["repo"]
    _fake_origin(repo, "git@github.com:acme/widgets.git")
    _write(repo / "src" / "brr" / "long.py", "".join(f"line {i}\n" for i in range(1, 301)))
    (repo / "src" / "brr" / "blob.bin").write_bytes(b"\x89PNG\0\0binary")
    snapshot = state.build(repo, machine["home"], now=NOW)
    page, read = pages.place_page(repo, machine["home"], "src/brr/real.py", snapshot)
    assert page["gh_url"] == "https://github.com/acme/widgets/blob/trunk/src/brr/real.py"
    assert page["text"] == {"from": 1, "to": 1, "total": 1, "binary": False, "text": "print('real')"}
    assert any(r.endswith(".git/config") for r in read)
    long, _ = pages.place_page(repo, machine["home"], "src/brr/long.py", snapshot)
    assert (long["text"]["from"], long["text"]["to"], long["text"]["total"]) == (1, 200, 300)
    ranged, _ = pages.place_page(repo, machine["home"], "src/brr/long.py", snapshot, text_from=250, text_to=260)
    assert ranged["text"]["text"].splitlines() == [f"line {i}" for i in range(250, 261)]
    blob, _ = pages.place_page(repo, machine["home"], "src/brr/blob.bin", snapshot)
    assert blob["text"] == {"from": None, "to": None, "total": None, "binary": True, "text": None}
    _fake_origin(repo, "https://gitlab.example/acme/widgets.git")
    assert pages.place_page(repo, machine["home"], "src/brr/real.py", snapshot)[0]["gh_url"] is None


def test_place_page_attention_from_the_beads(machine):
    snapshot = state.build(machine["repo"], machine["home"], now=NOW)
    page, _ = pages.place_page(machine["repo"], machine["home"], "src/brr/real.py", snapshot)
    # `sed -n 1,9p` on a one-line file: clamped to the file as it is
    assert page["attention"] == [
        {"from": 1, "to": 1, "kind": "read", "count": 1, "last": iso(NOW - 90), "src": "parsed"},
    ]


@pytest.fixture
def long_file(machine):
    _write(machine["repo"] / "src" / "brr" / "long.py", "".join(f"x = {i}\n" for i in range(100)))
    return machine


def _attention(machine, detail, tools=("Bash",)):
    where = state.locate(machine["repo"], machine["home"])
    row = {"act": "probe", "detail": detail, "cwd": str(machine["repo"]), "tools": list(tools)}
    return pages.attention_of_row(row, "src/brr/long.py", where, 100)


def test_attention_three_command_shapes_three_ranges(long_file):
    m = long_file
    P = "parsed"  # every range this path returns is read off a command line, not the file
    assert _attention(m, "sed -n '10,20p' src/brr/long.py")[0] == [{"from": 10, "to": 20, "kind": "read", "src": P}]
    assert _attention(m, "head -n 5 src/brr/long.py")[0] == [{"from": 1, "to": 5, "kind": "read", "src": P}]
    assert _attention(m, "tail -n 10 src/brr/long.py")[0] == [{"from": 91, "to": 100, "kind": "read", "src": P}]  # the last 10 as it is now
    assert _attention(m, "grep -n needle src/brr/long.py")[0] == [{"from": 1, "to": 100, "kind": "read", "whole": True, "src": P}]
    absolute = str(m["repo"] / "src" / "brr" / "long.py")
    assert _attention(m, absolute, tools=("Read",))[0] == [{"from": 1, "to": 100, "kind": "read", "whole": True, "src": P}]
    assert _attention(m, absolute, tools=("Edit",)) == ([], {"read": 0, "edit": 1})  # no range recorded: counted
    diff = "git diff src/brr/long.py\n+++ b/src/brr/long.py\n@@ -40,3 +40,6 @@ def f():"
    assert _attention(m, diff)[0] == [{"from": 40, "to": 45, "kind": "edit", "src": P}]
    assert _attention(m, "sed -n 1,5p src/brr/other.py")[0] == []  # another file


def test_attention_overlapping_ranges_merge(long_file):
    ranges = []
    for detail, at in (("sed -n 10,20p src/brr/long.py", "t1"), ("sed -n 15,30p src/brr/long.py", "t2"),
                       ("sed -n 31,33p src/brr/long.py", "t3"), ("sed -n 60,70p src/brr/long.py", "t4"),
                       ("grep -n x src/brr/long.py", "t5")):
        found, _ = _attention(long_file, detail)
        ranges += [{**r, "last": at} for r in found]
    assert pages.merge_ranges(ranges) == [
        {"from": 10, "to": 33, "kind": "read", "count": 3, "last": "t3", "src": "parsed"},  # overlapping and touching merge
        {"from": 60, "to": 70, "kind": "read", "count": 1, "last": "t4", "src": "parsed"},
        {"from": 1, "to": 100, "kind": "read", "count": 1, "last": "t5", "whole": True, "src": "parsed"},  # a grep swallows nothing
    ]


# ── the measured spans: git's hunks outrank the command line ─────────────


def test_attention_prefers_the_row_s_measured_chunks(long_file):
    """#2021 put git's real hunks on the row; this page used to parse the
    command text instead and file every edit under ``unranged``."""
    m = long_file
    where = state.locate(m["repo"], m["home"])
    absolute = str(m["repo"] / "src" / "brr" / "long.py")
    row = {
        "act": "mutate", "detail": absolute, "cwd": str(m["repo"]), "tools": ["Edit"],
        "chunks": [{"path": absolute, "rel": "src/brr/long.py", "from": 40, "to": 45, "kind": "write"}],
    }
    assert pages.attention_of_row(row, "src/brr/long.py", where, 100) == (
        [{"from": 40, "to": 45, "kind": "edit", "src": "measured"}], {"read": 0, "edit": 0},
    )
    # the positive control for the precedence: the SAME row without chunks is
    # the old answer — an unranged edit. Without this, the assertion above
    # could be passing on a path that never looked at `chunks` at all.
    assert pages.attention_of_row({k: v for k, v in row.items() if k != "chunks"},
                                  "src/brr/long.py", where, 100) == ([], {"read": 0, "edit": 1})
    # a write git could not span stays counted, never drawn as a line
    flagged = {**row, "chunks": [{"path": absolute, "rel": "src/brr/long.py", "changed": True, "kind": "write"}]}
    assert pages.attention_of_row(flagged, "src/brr/long.py", where, 100) == ([], {"read": 0, "edit": 1})
    # chunks that name another file do not speak for this one: the parser runs
    other = {**row, "detail": "sed -n '10,20p' src/brr/long.py", "tools": ["Bash"],
             "chunks": [{"path": str(m["repo"] / "src" / "brr" / "other.py"),
                         "rel": "src/brr/other.py", "from": 1, "to": 9, "kind": "read"}]}
    assert pages.attention_of_row(other, "src/brr/long.py", where, 100)[0] == [
        {"from": 10, "to": 20, "kind": "read", "src": "parsed"},
    ]
    # measured spans are clamped to the file as it is now, like parsed ones
    past_end = {**row, "chunks": [{"path": absolute, "rel": "src/brr/long.py",
                                   "from": 300, "to": 400, "kind": "write"}]}
    assert pages.attention_of_row(past_end, "src/brr/long.py", where, 100) == ([], {"read": 0, "edit": 0})


def test_merged_band_cannot_launder_a_guess_into_a_measurement():
    ranges = [
        {"from": 10, "to": 20, "kind": "edit", "src": "measured", "last": "t1"},
        {"from": 21, "to": 30, "kind": "edit", "src": "parsed", "last": "t2"},
        {"from": 60, "to": 70, "kind": "edit", "src": "measured", "last": "t3"},
    ]
    assert pages.merge_ranges(ranges) == [
        {"from": 10, "to": 30, "kind": "edit", "count": 2, "last": "t2", "src": "mixed"},
        {"from": 60, "to": 70, "kind": "edit", "count": 1, "last": "t3", "src": "measured"},
    ]


def test_place_page_scoped_to_one_run_carries_only_that_run(machine):
    snapshot = state.build(machine["repo"], machine["home"], now=NOW)
    whole, _ = pages.place_page(machine["repo"], machine["home"], "src/brr/real.py", snapshot)
    assert whole["scope"] is None and whole["scope_known"] is None
    assert whole["attention"], "control: the unscoped page has attention to lose"

    mine, _ = pages.place_page(machine["repo"], machine["home"], "src/brr/real.py", snapshot, run=RUN)
    assert mine["scope"] == RUN and mine["scope_known"] is True
    assert {p["run"] for p in mine["passes"]} <= {RUN}
    assert {b["run"] for b in mine["beads"]} <= {RUN}

    stranger = "run-260101-0000-zzzz"
    empty, _ = pages.place_page(machine["repo"], machine["home"], "src/brr/real.py", snapshot, run=stranger)
    assert empty["scope"] == stranger and empty["scope_known"] is False
    assert empty["attention"] == [] and empty["beads"] == [] and empty["passes"] == []
    # the file itself is still the file: scoping the record does not blank the text
    assert empty["text"] == whole["text"]


def test_pass_page_names_its_own_bound(kid_contract):
    page, _ = pages.pass_page(kid_contract["repo"], kid_contract["home"], CHILD)
    assert page["bead_total"] is not None
    assert page["bead_total"] >= len(page["beads"])


def test_pass_page_says_how_deep_its_place_scan_went(kid_contract):
    page, _ = pages.pass_page(kid_contract["repo"], kid_contract["home"], CHILD)
    assert page["places_scanned"] is not None
    # the scan's bottom is a bound a reader compares to the run's whole life
    assert page["places_scanned"] <= page["bead_total"]
    assert all("path" in e and "touches" in e for e in page["places"])
