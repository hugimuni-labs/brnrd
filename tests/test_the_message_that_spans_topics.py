"""Move 5e — the message that spans topics (design-the-loom §21, amendment 2 (e)+(f)).

(e) An inbound message may be assigned to several topics by the reply's
``topic:`` list: one index row per topic under the same event id; every act
of the weaver still belongs to exactly one.

(f) An unstamped inbound event shows at the boundary, the way a finished
strand does, until a reply's ``topic:``, a ``note:`` carrying ``topic:``, or
(for the waking event) ``.topic`` stamps it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import daemon, heddles, protocol, run_topic

from test_the_event_carries_its_topic import _seat, _topic
from test_the_topic_that_minted_itself import _drain_again


@pytest.fixture(autouse=True)
def _clean():
    run_topic._UNSTAMPABLE.clear()
    yield
    run_topic._UNSTAMPABLE.clear()


def _refs(home: Path, slug: str, kind: str) -> list[str]:
    return [row["ref"] for row in heddles.index(home, slug) if row["kind"] == kind]


def _letter(got: dict, body: str = "about the loom and the post", **meta) -> Path:
    return protocol.create_event(got["inbox"], "telegram", body, telegram_user_id="42",
                                 telegram_chat_id="42", telegram_user="Gurio", **meta)


def _reply(tmp_path: Path, got: dict, frontmatter: str, body: str = "the answer\n") -> None:
    (got["outbox"] / "reply.md").write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")
    _drain_again(tmp_path, got)
    got["notices"] = daemon._read_outbox_notices(got["outbox"])


# ── (e) the list grammar ────────────────────────────────────────────────


def test_the_list_splits_on_commas_dots_and_spaces():
    assert run_topic.topic_words("the-loom, the-post") == ["the-loom", "the-post"]
    assert run_topic.topic_words("the-loom·the-post  x") == ["the-loom", "the-post", "x"]
    assert run_topic.first_topic("the-loom the-post") == "the-loom"
    assert run_topic.first_topic("") is None


def test_a_two_topic_reply_stamps_both_and_indexes_the_event_in_each(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    home = got["home"]
    letter = _letter(got)
    _reply(tmp_path, got, f"event: {letter.stem}\ntopic: the-post, the-loom\n")

    event = protocol._read_event(letter)
    assert event["topic"] == "the-post the-loom"  # space-separated, canonical, in order
    assert _refs(home, "the-post", "event") == [letter.stem]
    assert letter.stem in _refs(home, "the-loom", "event")
    # the reply itself is one act: its message row lands in the first topic only
    assert len(_refs(home, "the-post", "message")) == 1
    assert _refs(home, "the-loom", "message") == []
    # the thread remembers the first
    assert heddles.thread_topic(home, "telegram:42:") == "the-post"
    # the answered event is inherited by its first
    assert run_topic.answered_topic(
        {"event": letter.stem}, got["task"], account_home=home,
        resolve_event=lambda _id: protocol._read_event(letter),
    ) == "the-post"
    assert not [n for n in got["notices"] if n["kind"] in ("dropped", "refused")]


def test_an_unknown_slug_in_the_list_is_dropped_with_one_advisory(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    home = got["home"]
    letter = _letter(got)
    _reply(tmp_path, got, f"event: {letter.stem}\ntopic: nope, the-loom, also-nope, the-post\n")

    assert protocol._read_event(letter)["topic"] == "the-loom the-post"
    assert letter.stem in _refs(home, "the-loom", "event")
    assert _refs(home, "the-post", "event") == [letter.stem]
    assert not (heddles.topics_dir(home) / "nope.index.jsonl").exists()
    dropped = [n for n in got["notices"] if "names no heddle" in n["text"]]
    assert len(dropped) == 1 and dropped[0]["kind"] == "advisory"
    assert "nope, also-nope" in dropped[0]["text"] and "the-loom, the-post" in dropped[0]["text"]
    # delivered, not swallowed by the verb row
    assert len(_refs(home, "the-loom", "message")) == 1


def test_an_already_stamped_event_is_never_reclassified_by_a_list(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    letter = _letter(got, topic="the-post")
    _reply(tmp_path, got, f"event: {letter.stem}\ntopic: the-loom, the-post\n")
    assert protocol._read_event(letter)["topic"] == "the-post"
    assert letter.stem not in _refs(got["home"], "the-loom", "event")


def test_also_targets_are_stamped_with_the_same_list(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    home = got["home"]
    first = _letter(got, "one")
    second = _letter(got, "two")
    _reply(tmp_path, got, f"event: {first.stem}\nalso: {second.stem}\ntopic: the-post the-loom\n")

    for letter in (first, second):
        assert protocol._read_event(letter)["topic"] == "the-post the-loom"
        assert letter.stem in _refs(home, "the-post", "event")
        assert letter.stem in _refs(home, "the-loom", "event")
    assert protocol._read_event(second)["status"] == "done"
    assert len(_refs(home, "the-post", "message")) == 1


def test_confirm_event_takes_a_list_and_a_plain_slug(tmp_path):
    home = tmp_path / "home"
    _topic(home, "the-loom")
    _topic(home, "the-post")
    inbox = tmp_path / "inbox"
    one = protocol.create_event(inbox, "telegram", "x")
    two = protocol.create_event(inbox, "telegram", "y")
    assert run_topic.confirm_event(home, inbox, one.stem, "the-loom", thread="t:1")
    assert protocol._read_event(one)["topic"] == "the-loom"
    assert run_topic.confirm_event(home, inbox, two.stem, ["the-post", "the-loom", "the-post"],
                                   thread="t:2")
    assert protocol._read_event(two)["topic"] == "the-post the-loom"
    assert heddles.thread_topic(home, "t:2") == "the-post"
    assert not run_topic.confirm_event(home, inbox, two.stem, ["the-loom"])  # once


# ── (f) the unstamped event at the boundary ─────────────────────────────


def test_an_event_already_stamped_never_shows(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    _letter(got, topic="the-post")
    pending = daemon._pending_events_for_agent(got["inbox"], got["task"].event_id)
    assert run_topic.unstamped_events(pending) == []


def _pending_unstamped(got: dict) -> list[str]:
    pending = daemon._pending_events_for_agent(got["inbox"], got["task"].event_id)
    return [row["id"] for row in run_topic.unstamped_events(pending)]


def test_the_line_appears_and_a_reply_topic_stamps_it_away(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    letter = _letter(got, topic_suggested="deck-v13")
    assert _pending_unstamped(got) == [letter.stem]
    _reply(tmp_path, got, f"event: {letter.stem}\ntopic: the-post\n")
    assert protocol._read_event(letter)["topic"] == "the-post"
    assert _pending_unstamped(got) == []


def test_note_with_topic_stamps_the_event_and_retires_it(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    home = got["home"]
    letter = _letter(got, topic_suggested="deck-v13")
    assert _pending_unstamped(got) == [letter.stem]
    (got["outbox"] / "note.md").write_text(
        f"---\nnote: {letter.stem}\ntopic: the-post, nope, the-loom\n---\n", encoding="utf-8")
    _drain_again(tmp_path, got)
    notices = daemon._read_outbox_notices(got["outbox"])

    event = protocol._read_event(letter)
    assert event["status"] == "noted"
    assert event["topic"] == "the-post the-loom"
    assert _refs(home, "the-post", "event") == [letter.stem]
    assert letter.stem in _refs(home, "the-loom", "event")
    assert heddles.thread_topic(home, "telegram:42:") == "the-post"
    # a note speaks no message: no message rows anywhere
    assert _refs(home, "the-post", "message") == []
    dropped = [n for n in notices if "names no heddle" in n["text"]]
    assert len(dropped) == 1 and dropped[0]["kind"] == "advisory" and "nope" in dropped[0]["text"]
    assert _pending_unstamped(got) == []


def test_note_without_topic_retires_as_before_and_stamps_nothing(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    letter = _letter(got)
    (got["outbox"] / "note.md").write_text(f"---\nnote: {letter.stem}\n---\n", encoding="utf-8")
    _drain_again(tmp_path, got)
    event = protocol._read_event(letter)
    assert event["status"] == "noted" and "topic" not in event


def test_note_on_an_already_stamped_event_says_so_and_reclassifies_nothing(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control="the-loom")
    letter = _letter(got, topic="the-loom")
    (got["outbox"] / "note.md").write_text(
        f"---\nnote: {letter.stem}\ntopic: the-post\n---\n", encoding="utf-8")
    _drain_again(tmp_path, got)
    assert protocol._read_event(letter)["topic"] == "the-loom"
    notices = daemon._read_outbox_notices(got["outbox"])
    assert any("already stamped the-loom" in n["text"] for n in notices)


def test_the_waking_event_shows_until_topic_stamps_it(tmp_path):
    from brr import hud
    from brr.run import Run

    home = tmp_path / "home"
    _topic(home, "the-loom")
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    own = protocol.create_event(inbox, "telegram", "the deck v13", telegram_user="Gurio",
                                topic_suggested="the-deck-v13")
    task = Run.from_event(protocol._read_event(own))
    task.conversation_key = "telegram:42:"
    (row,) = hud._unstamped(task, [])
    assert run_topic.unstamped_line(row) == \
        f"✉ {own.stem.rsplit('-', 1)[-1]} from Gurio · topic: none matched — new the-deck-v13?"
    (outbox / ".topic").write_text("the-loom\n", encoding="utf-8")
    run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox)
    assert protocol._read_event(own)["topic"] == "the-loom"
    assert hud._unstamped(task, []) == []


def test_null_answers_the_waking_event_and_a_strand_shows_nothing(tmp_path):
    from brr import hud
    from brr.run import Run

    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    own = protocol.create_event(inbox, "telegram", "chatter", telegram_user="Gurio")
    task = Run.from_event(protocol._read_event(own))
    other = {"id": "evt-9-zzzz", "source": "telegram", "telegram_user": "Gurio"}
    assert [r["id"] for r in hud._unstamped(task, [other])] == [own.stem, "evt-9-zzzz"]
    (outbox / ".topic").write_text("null\n", encoding="utf-8")
    run_topic.settle(task, outbox_dir=outbox, account_home=tmp_path / "home", inbox_dir=inbox)
    assert [r["id"] for r in hud._unstamped(task, [other])] == ["evt-9-zzzz"]
    strand = Run(id="run-s", event_id="evt-8-ssss", body="", source="spawn",
                 meta={"strand": True, "spawn_parent_run_id": "run-p"})
    assert hud._unstamped(strand, [other]) == []
