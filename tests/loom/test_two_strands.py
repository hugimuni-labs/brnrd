from _step import mail
"""Two strands: mid-work letters are answered or returned to their author."""

import json
import os
import signal

from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import _ingest
from brr.loom.runtime.project import holder

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


def test_a_letter_mid_work_is_answered_and_a_killed_bodys_letter_bounces(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "ta", "thread ta", "answer-pings", wait="20")
    write_thread(root, "tb", "thread tb\ntarget: ta", "ping-two", wait="20")
    from brr.loom.runtime.ledger import inject_letter
    inject_letter(root, to="thread:ta", body="begin")
    inject_letter(root, to="thread:tb", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    caught = {"killed": False}
    try:
        def drive():
            facts = loom.facts()
            held = holder(facts, "ta")
            if not caught["killed"] and held is not None:
                strand = held[0]
                room = root / "rooms" / strand
                starts = [f for f in facts if f.kind == "body.started"
                          and f.data.get("strand") == strand]
                pings = _body(facts, "ping-2")
                if (starts and pings and not _replies(facts, pings[0].data["id"], "2-gnip")
                        and not _pending_reply(room, pings[0].data["id"])
                        and (_shown(facts, pings[0].data["id"], strand)
                             or _shown_file(room, pings[0].data["id"]))):
                    os.killpg(int(starts[0].data["pid"]), signal.SIGKILL)
                    caught.update(killed=True, strand=strand, room=room)
            pings = _body(facts, "ping-2")
            if not caught["killed"] or not pings:
                return False
            bounce = [f for f in facts if f.kind == "letter" and f.data.get("from") == "loom"
                      and pings[0].data["id"] in f.data.get("body", "")]
            return bool(bounce and _shown(facts, bounce[0].data["id"], pings[0].data["from"]))

        wait_until(drive, 25, loom.dump)
        facts = loom.facts()
        ping1 = _body(facts, "ping-1")[0]
        assert min(_trace_ns(caught["room"], ping1.data["id"])) < 15
        assert len(_replies(facts, ping1.data["id"], "1-gnip")) == 1
        ping2 = _body(facts, "ping-2")[0]
        assert not _replies(facts, ping2.data["id"], "2-gnip")
        assert not mail(root, facts, "ta")
        starts = [f for f in facts if f.kind == "body.started"
                  and f.data.get("strand") == caught["strand"]]
        assert len(starts) == 1
        exits = [f for f in facts if f.kind == "body.exited"
                 and f.data.get("code") == -signal.SIGKILL]
        assert len(exits) == 1
        assert exits[0].data["strand"] == caught["strand"]
    finally:
        loom.halt()
    assert not loom.errors
    assert loom.out_files() == []
    home_facts = read_facts(loom.home)
    owed_before = [fact.id for fact in mail(root, home_facts, "ta")]
    held_before = holder(home_facts, "ta")
    _ingest(loom.home)
    again = read_facts(loom.home)
    assert [fact.id for fact in again] == [fact.id for fact in home_facts]
    assert [fact.id for fact in mail(root, again, "ta")] == owed_before
    assert holder(again, "ta") == held_before
    assert loom.out_files() == []
