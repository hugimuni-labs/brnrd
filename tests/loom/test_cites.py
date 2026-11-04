"""Receipts at the stop (threads/trust-guards C2): --cites on settle and reincarnate, uncited channel sends."""

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.cites import _within, channel_sends, check_cite, missed, section
from brr.loom.runtime.port import PortError


def _letter(ident, to="thread:inbox", **data):
    return Fact(kind="letter", by="loom:aaaa", id=ident,
                data={"id": ident, "body": "x", "from": "person:arseni", "to": to, **data})


def _wish(ident, thread, says):
    return Fact(kind="wish", by="strand:s-aaaa-inbox", id=ident, at="2026-11-01T00:00:00+00:00",
                data={"wish": ident, "from": "person", "line": "x", "thread": thread,
                      "says": [{"letter": s} for s in says]})


@pytest.mark.parametrize("facts,letter,ok", [
    ([_letter("L1", to="thread:ta")], "L1", True),
    ([_letter("L1"), _wish("w-1", "ta", ["L1"])], "L1", True),
    ([_letter("L1", wish="w-1"), _wish("w-1", "ta", [])], "L1", True),
    # A letter to another thread, on none of this thread's wishes, is no receipt here.
    ([_letter("L1"), _wish("w-1", "other", ["L1"])], "L1", False),
    ([_letter("L1", to="thread:ta")], "L2", False),
])
def test_a_cite_is_a_letter_on_the_thread(facts, letter, ok):
    if ok:
        check_cite(facts, letter, "ta")
    else:
        with pytest.raises(PortError, match=f"--cites {letter}"):
            check_cite(facts, letter, "ta")


@pytest.mark.parametrize("channel,ok", [
    ("channel:cloud/telegram:155783668", True),
    ("channel:cloud/telegram:155783668:42", True),
    ("channel:cloud/telegram:1557836680", False),
    ("channel:cloud/telegram:99", False),
])
def test_a_topic_sits_inside_its_chat_and_an_id_is_never_cut(channel, ok):
    assert _within(channel, {"channel:cloud/telegram:155783668"}) is ok


def test_misses_reads_only_its_own_section(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# t\n\nsend:channel:a@L9 in prose\n\n## Misses\n\n- send:channel:b@L1: said, not asked\n\n## Plan\n")
    assert "send:channel:b@L1" in missed(readme)
    assert "send:channel:a@L9" not in missed(readme)
    assert missed(tmp_path / "none.md") == ""


def test_an_unsent_channel_letter_without_re_is_listed(tmp_path):
    out = tmp_path / "room" / "port" / "out"
    out.mkdir(parents=True)
    (out / "a.md").write_text("---\nkind: letter\nid: L1\nto: channel:cloud/telegram:1\n---\nhi\n")
    (out / "b.md").write_text("---\nkind: letter\nid: L2\nto: channel:cloud/telegram:1\nre: L0\n---\nhi\n")
    (out / "c.md").write_text("---\nkind: letter\nid: L3\nto: thread:inbox\n---\nhi\n")
    assert channel_sends(tmp_path / "room", "s-aaaa-ta", 0) == [{"id": "L1", "to": "channel:cloud/telegram:1"}]


def test_a_check_that_cannot_run_never_costs_the_stop(tmp_path, monkeypatch):
    monkeypatch.delenv("BRNRD_THREAD", raising=False)
    assert section(tmp_path) == ""
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    assert section(tmp_path / "no-room") == ""
