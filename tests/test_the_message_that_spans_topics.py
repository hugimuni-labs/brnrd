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
from brr.outbox import table
from brr.outbox import topic as topic_verb

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


def test_the_selector_lets_a_slug_list_through_on_a_reply_only():
    reply = {"event": "evt-1", "topic": "the-loom, the-post"}
    burst = {"event": "evt-1", "also": "evt-2", "topic": "the-loom the-post"}
    assert not table._selects_topic(reply)
    assert not table._selects_topic(burst)
    # an op word first is still the verb's, reply or not
    assert table._selects_topic({"event": "evt-1", "topic": "new the-post"})
    assert table._selects_topic({"event": "evt-1", "topic": "assign the-post -> evt-2"})
    # a typo'd op on a bare file is still claimed (and dropped by the verb)
    assert table._selects_topic({"topic": "rename the-loom the-weave"})
    assert table._selects_topic({"topic": "the-loom, the-post"})
    # `topic: assign` keeps its one-slug grammar
    assert topic_verb.parse("assign the-loom the-post -> evt-1789471812449784000-ab12") is None
    assert topic_verb.parse("assign the-loom -> evt-1789471812449784000-ab12")[1] == ["the-loom"]


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


def test_listed_topics_render_in_the_event_line_and_the_bundle(tmp_path):
    from brr import prompts

    assert run_topic.event_topic_line({"topic": "the-loom the-post"}) == \
        "topic: `the-loom`, `the-post`"
    (line,) = prompts._topic_bundle_lines({"topic": "the-loom the-post"})
    assert line.startswith("- Topic: `the-loom`, `the-post` — assigned")
