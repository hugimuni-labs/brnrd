"""A wish receipt follows the opener's grant through the real send and wish verbs."""
from dataclasses import replace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _ingest
from brr.loom.runtime.selfrepo import room
from brr.loom.runtime.wishes import wishes
from test_reply_address import _relayed
from test_wake import cli
from test_wishes import command, ledger  # noqa: F401 - fixture


def _seat(home, thread, strand, gen=1):
    clone = room(home.root, strand)
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": thread, "strand": strand, "gen": gen,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))
    return clone.parent


@pytest.mark.parametrize(("route", "target"), [
    ("opener", "thread:work"),
    ("grant", "channel:relay/telegram:42"),
    ("face", "channel:relay/telegram:42"),
])
def test_delivery_reaches_only_its_authorized_recipient(ledger, route, target):
    home, opener = ledger
    (home.root / "loom/config.toml").write_text('connectors = ["relay"]\n')
    face = route == "face"
    seat = _seat(home, "inbox", "s-aaaa-face") if face else opener
    source, letter = _relayed("telegram")
    append(home, source)
    append(home, replace(letter, data=dict(letter.data, to="thread:inbox" if face else "thread:work")))
    ident = command(home, seat, "wish", "new", "--line", "A result", "--say", letter.id)
    if not face:
        grant = ["--may-deliver"] if route == "grant" else []
        command(home, opener, "send", "--to", "thread:worker", "--wish", ident, *grant, "Do the work")
        # The delivery body reads its grant from the ledger, even in a later generation.
        seat = _seat(home, "worker", "s-aaaa-worker", gen=2)

    result = cli(["wish", "--room", str(seat), ident, "--result", "url:https://example.com/result",
                  "--line", "Finished", "--deliver"])
    assert result.returncode == 0, result.stderr
    _ingest(home)
    facts = read_facts(home)
    assert wishes(facts)[ident].state == "delivered"
    receipts = [f for f in facts if f.kind == "letter" and
                f.data.get("body", "").startswith(f"delivered &{ident}:")]
    assert [receipt.data["to"] for receipt in receipts] == [target]
    if route == "opener":
        assert result.stderr.strip() == (
            "delivered to work, not to the person: the opener did not grant delivery")
        assert "Reply ok" not in receipts[0].data["body"]
    else:
        assert "Reply ok" in receipts[0].data["body"]


def test_delivery_permission_cannot_be_added_to_an_existing_thread(ledger):
    home, opener = ledger
    error = command(home, opener, "send", "--to", "thread:other", "--may-deliver", "Do the work", ok=False)
    assert "--may-deliver is only for opening a new thread" in error
