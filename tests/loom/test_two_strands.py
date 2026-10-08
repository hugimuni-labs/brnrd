"""Two strands, one letter mid-work, one kill, one successor."""

import json
import os
import signal

from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import _ingest
from brr.loom.runtime.project import holder, owed

from _step import Loom, wait_until, write_thread


def _body(facts, text):
    found = [fact for fact in facts if fact.kind == "letter" and fact.data.get("body") == text]
    return found


def _replies(facts, letter_id, body):
    return [
        fact for fact in facts
        if fact.kind == "letter" and fact.data.get("re") == letter_id
        and fact.data.get("body") == body
    ]


def _shown(facts, letter_id, strand):
    return [
        fact for fact in facts
        if fact.kind == "shown" and fact.data.get("strand") == strand
        and letter_id in (fact.data.get("ids") or [])
    ]


def _pending_reply(room, letter_id) -> bool:
    out = room / "port" / "out"
    if not out.is_dir():
        return False
    for path in out.glob("*.md"):
        text = path.read_text(errors="replace")
        if "kind: letter" in text and f"re: {letter_id}" in text:
            return True
    return False


def _shown_file(room, letter_id) -> bool:
    out = room / "port" / "out"
    if not out.is_dir():
        return False
    for path in out.glob("*.md"):
        text = path.read_text(errors="replace")
        if "kind: shown" in text and letter_id in text:
            return True
    return False


def _trace_ns(room, letter_id):
    path = room / "port" / "trace.jsonl"
    if not path.is_file():
        return []
    found = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if letter_id in row.get("ids", []):
            found.append(row["n"])
    return found


def test_a_letter_mid_work_and_a_killed_body_answered_once_by_its_successor(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "ta", "thread ta", "answer-pings", wait="20")
    write_thread(root, "tb", "thread tb\ntarget: ta", "ping-two", wait="20")
    from brr.loom.runtime.ledger import inject_letter
    inject_letter(root, to="thread:ta", body="begin")
    inject_letter(root, to="thread:tb", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    caught = {"wake": None, "killed": False}
    try:
        def drive():
            facts = loom.facts()
            held = holder(facts, "ta")
            if held is None:
                return False
            strand = held[0]
            room = root / "rooms" / strand
            starts = [
                fact for fact in facts
                if fact.kind == "body.started" and fact.data.get("strand") == strand
            ]
            if len(starts) >= 2 and caught["wake"] is None:
                caught["wake"] = (room / "port" / "wake.md").read_text()
            pings = _body(facts, "ping-2")
            if (not caught["killed"] and len(starts) == 1 and pings
                    and not _replies(facts, pings[0].data["id"], "2-gnip")
                    and not _pending_reply(room, pings[0].data["id"])
                    and (_shown(facts, pings[0].data["id"], strand)
                         or _shown_file(room, pings[0].data["id"]))):
                os.killpg(int(starts[0].data["pid"]), signal.SIGKILL)
                caught["killed"] = True
                caught["pid"] = int(starts[0].data["pid"])
            if not caught["killed"] or caught["wake"] is None:
                return False
            ping1 = _body(facts, "ping-1")
            if len(ping1) != 1 or len(_replies(facts, ping1[0].data["id"], "1-gnip")) != 1:
                return False
            if len(_replies(facts, pings[0].data["id"], "2-gnip")) != 1:
                return False
            deaths = [
                fact for fact in facts
                if fact.kind == "body.died" and fact.data.get("strand") == strand
            ]
            return any(fact.data.get("code") == -signal.SIGKILL for fact in deaths)

        wait_until(drive, 25, loom.dump)
        facts = loom.facts()
        strand, gen = holder(facts, "ta")
        room = root / "rooms" / strand
        ping1 = _body(facts, "ping-1")[0]
        assert min(_trace_ns(room, ping1.data["id"])) < 15
        reply1 = _replies(facts, ping1.data["id"], "1-gnip")
        assert len(reply1) == 1
        assert _shown(facts, reply1[0].data["id"], ping1.data["from"])
        ping2 = _body(facts, "ping-2")[0]
        assert len(_replies(facts, ping2.data["id"], "2-gnip")) == 1
        assert ping2.data["id"] in caught["wake"]
        assert "ping-2" in caught["wake"]
        starts = [
            fact for fact in facts
            if fact.kind == "body.started" and fact.data.get("strand") == strand
        ]
        assert len(starts) >= 2
        assert starts[0].data["gen"] == starts[1].data["gen"] == gen
        assert starts[0].data["pid"] != starts[1].data["pid"]
        assert starts[0].data["pid"] == caught["pid"]
        died = [
            fact for fact in facts
            if fact.kind == "body.died" and fact.data.get("code") == -signal.SIGKILL
        ]
        assert len(died) == 1
        assert died[0].data["strand"] == strand
    finally:
        loom.halt()
    assert not loom.errors
    assert loom.out_files() == []
    home_facts = read_facts(loom.home)
    owed_before = [fact.id for fact in owed(home_facts, "ta")]
    held_before = holder(home_facts, "ta")
    _ingest(loom.home)
    again = read_facts(loom.home)
    assert [fact.id for fact in again] == [fact.id for fact in home_facts]
    assert [fact.id for fact in owed(again, "ta")] == owed_before
    assert holder(again, "ta") == held_before
    assert loom.out_files() == []
