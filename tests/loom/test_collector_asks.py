"""A fuel collector's error reaches the person as an ask, through the tick."""
import json

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import collectors
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.router import Router
from brr.loom.runtime.wishes import wishes
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)


@pytest.fixture
def home(ledger):
    home, seat = ledger
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    (home.root / "self/people/ada").mkdir(parents=True)
    (home.root / "self/people/ada/channels.toml").write_text('channels = ["mine:42"]\n')
    collectors._SEEN.clear()
    yield home, seat
    collectors._SEEN.clear()


_AT = iter(range(1000, 10**6, 600))


def reads(home, shell, error=None, windows=()):
    """What a usage refresh writes, then one tick reads it."""
    path = home.root / "loom/usage" / f"{shell}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"at": next(_AT), "windows": [{"name": n, "left_pct": 50, "resets_at": None} for n in windows]}
    if error:
        payload["error"] = error
    path.write_text(json.dumps(payload) + "\n")
    tick_once(home, {}, "fake", "haiku", router=Router(home))


def asks(home, shell="grok"):
    return sorted((w for w in wishes(read_facts(home)).values()
                   if w.line.startswith(f"fuel: the {shell} collector")), key=lambda w: w.born)


def mails(home, ident):
    return [f for f in read_facts(home) if f.kind == "letter" and ident in (f.data.get("links") or ())]


def test_an_error_held_two_readings_is_one_ask_mailed_with_its_fix(home):
    home, _seat = home
    reads(home, "grok", "grok billing HTTP 401; no authentication refresh attempted")
    assert asks(home) == []  # one reading is a blip
    reads(home, "grok", "grok billing HTTP 401; no authentication refresh attempted")
    ask, = asks(home)
    assert ask.open and ask.sender == "resident" and ask.thread == "inbox"
    assert "HTTP 401" in ask.line and "grok login" in ask.line
    parent = wishes(read_facts(home))[ask.parent]
    assert parent.line == collectors.FUEL_WISH and parent.sender == "resident"
    mail, = mails(home, ask.id)
    assert mail.data["to"] == "channel:mine/42" and mail.data["from"] == "loom"
    assert f"ask {ask.id} from thread:inbox: " in mail.data["body"]
    assert f"answer &{ask.id}" in mail.data["body"]
    # The same error, read again and again, files nothing more.
    reads(home, "grok", "grok billing HTTP 401; no authentication refresh attempted")
    reads(home, "grok", "grok billing HTTP 401; no authentication refresh attempted")
    assert [a.id for a in asks(home)] == [ask.id] and len(mails(home, ask.id)) == 1
    # His reply to the mail, bound by the connector's receipt, answers the ask.
    from brr.loom.runtime.connector import ingest
    append(home, Fact(kind="speech.part", by="loom:aaaa", id="part:42:9", data={
        "key": mail.data["id"], "channel": "channel:mine/42", "receipt": {"message_id": "42:9"}}))
    ingest(home, "mine", dict(id="r1", kind="letter", to="channel:mine/42", **{"from": "mine:42"},
                              re="42:9", body="logged in again"), [])
    assert asks(home)[0].state == "answered"


def test_a_clean_reading_ends_the_ask_and_the_error_back_asks_again(home):
    home, _seat = home
    for _ in range(2):
        reads(home, "claude", "no quota buckets parsed from /usage screen")
    first, = asks(home, "claude")
    assert "/login" in first.line
    # No windows and no error is an unknown gauge, not a cure.
    reads(home, "claude")
    assert asks(home, "claude")[0].open
    reads(home, "claude", windows=["session"])
    first, = asks(home, "claude")
    assert first.state == "dropped" and first.verdict["ref"].startswith(collectors.MOOT)
    for _ in range(2):
        reads(home, "claude", "no quota buckets parsed from /usage screen")
    old, again = asks(home, "claude")
    assert old.id == first.id and again.open and again.line == first.line


def test_a_different_error_ends_the_old_ask_and_files_a_fresh_one(home):
    home, _seat = home
    for _ in range(2):
        reads(home, "codex", "token expired")
    old, = asks(home, "codex")
    for _ in range(2):
        reads(home, "codex", "account unreachable")
    old, new = asks(home, "codex")
    assert old.state == "dropped" and new.open and "account unreachable" in new.line
    assert "codex login" in new.line
    # Another collector's asks are its own, under the same standing wish.
    for _ in range(2):
        reads(home, "grok", "grok billing request failed")
    grok, = asks(home, "grok")
    assert grok.parent == new.parent and asks(home, "codex")[1].open


def test_an_ask_the_person_dropped_is_not_filed_again_while_the_error_holds(home):
    home, _seat = home
    for _ in range(2):
        reads(home, "grok", "invalid grok credential or billing response")
    ask, = asks(home)
    append(home, Fact(kind="wish.verdict", by="person:ada", id="wish.verdict:mine", data={
        "wish": ask.id, "as": "dropped", "evidence": "word", "ref": "later"}))
    for _ in range(3):
        reads(home, "grok", "invalid grok credential or billing response")
    assert [a.id for a in asks(home)] == [ask.id]
