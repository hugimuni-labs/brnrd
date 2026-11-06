"""A done can be a file on the host: stored at --result, carried where the channel takes files."""
import hashlib
from types import SimpleNamespace

import pytest

from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.port import PortError, fact_from_port
from test_wishes import command, heard, ledger  # noqa: F401 - the fixture


def test_a_host_result_stores_the_bytes_and_records_what_they_are(ledger, tmp_path):
    home, seat = ledger
    ident = heard(home, seat, "A report")
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF the report")
    command(home, seat, "wish", ident, "--result", f"host:{report}", "--line", "The report", "--deliver")
    result = next(f for f in read_facts(home) if f.kind == "wish.result").data
    sha = hashlib.sha256(b"%PDF the report").hexdigest()
    assert result == dict(result, kind="host", ref=str(report.resolve()), sha256=sha, size=15,
                               name="report.pdf", mime="application/pdf", delivered=True)
    # The done is the bytes as delivered, whatever sits at the path later.
    report.write_bytes(b"rewritten")
    assert (home.root / "blobs" / sha).read_bytes() == b"%PDF the report"


def test_a_host_result_refuses_what_is_not_a_file(ledger, tmp_path):
    home, seat = ledger
    ident = heard(home, seat, "A report")
    for ref in (str(tmp_path), str(tmp_path / "missing")):
        said = command(home, seat, "wish", ident, "--result", f"host:{ref}", "--line", "x", ok=False)
        assert "no regular file" in said
    assert not any(f.kind == "wish.result" for f in read_facts(home))


def test_the_delivery_letter_names_the_file_and_stamps_it(monkeypatch, tmp_path):
    import brr.loom.runtime.port as port
    import brr.loom.runtime.project as project
    import brr.loom.runtime.verbs_loom as verbs_loom
    from brr.loom.runtime import verbs_wishes
    from brr.loom.runtime.wishes import Say
    sent = []
    monkeypatch.setattr(verbs_wishes, "read_facts", lambda home: [])
    monkeypatch.setattr(project, "reply_channels", lambda facts, home: {"L1": "channel:mine/42"})
    monkeypatch.setattr(port, "write_send", lambda room, **kw: sent.append(kw))
    monkeypatch.setattr(verbs_loom, "_room", lambda raw: tmp_path / "s-aaaa-work")
    monkeypatch.setattr(verbs_wishes, "strand_of_room", lambda room: "s-aaaa-work")
    wish = SimpleNamespace(id="w-want", line="A report", sender="person", says=[Say("L1")])
    sha = "ab" * 32
    verbs_wishes._tell_delivered(None, SimpleNamespace(room=None), wish, dict(
        kind="host", ref="/h/report.pdf", line="The report", sha256=sha, size=1258291, name="report.pdf"))
    assert sent[0]["body"] == ("delivered &w-want: A report\nThe report: /h/report.pdf\n"
                               f"file: report.pdf · 1.2 MB · sha256 {sha}\n"
                               "Reply ok to accept, or say what is off.\n")
    assert sent[0]["attach"] == f"{sha} report.pdf"


def test_the_loom_takes_a_file_only_on_a_channel_letter_from_its_store(tmp_path):
    room = tmp_path / "home" / "rooms" / "s-aaaa-work"
    room.mkdir(parents=True)
    sha = hashlib.sha256(b"x").hexdigest()
    letter = dict(kind="letter", id="s-aaaa-work/abcde", to="channel:mine/42", body="hi",
                  attach=f"{sha} x.txt")
    with pytest.raises(PortError, match="no blob"):
        fact_from_port(letter, "s-aaaa-work", 1, room=room)
    (tmp_path / "home" / "blobs").mkdir()
    (tmp_path / "home" / "blobs" / sha).write_bytes(b"x")
    assert fact_from_port(letter, "s-aaaa-work", 1, room=room).data["attach"] == f"{sha} x.txt"
    with pytest.raises(PortError, match="only on a letter to a channel"):
        fact_from_port(dict(letter, to="thread:other"), "s-aaaa-work", 1, room=room)
    with pytest.raises(PortError, match="expected <sha256> <name>"):
        fact_from_port(dict(letter, attach="../../etc/passwd x"), "s-aaaa-work", 1, room=room)
