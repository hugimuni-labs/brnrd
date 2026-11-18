"""A message to a person is checked at send against how they read (brnrd#2776), and an
empty body goes nowhere (brnrd#2774). Every case runs the real ``send`` verb on a temp home."""
import os
import time

import pytest

from brr.loom.runtime import audit
from brr.loom.runtime.ledger import append, inject_letter
from brr.loom.runtime.port import fact_from_port, parse_frontmatter

from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)

TO = "channel:mine/42"


@pytest.fixture
def person(ledger, monkeypatch):
    home, seat = ledger
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    (home.root / "self/people/ada").mkdir(parents=True)
    (home.root / "self/people/ada/channels.toml").write_text('channels = ["mine:42"]\n')
    monkeypatch.delenv(audit.SAY, raising=False)
    return home, seat


def reads(home, text):
    (home.root / "self/people/ada/reading.toml").write_text(text)


def send(home, seat, body, *flags, ok=True):
    return command(home, seat, "send", "--to", TO, *flags, body, ok=ok)


def sent(seat):
    return sorted((seat / "port" / "out").glob("*.md"))


def test_over_the_longest_message_is_refused_and_at_it_passes(person):
    home, seat = person
    reads(home, "max_chars = 10\n")
    said = send(home, seat, "x" * 11, ok=False)
    assert "reading: 11 chars, ada's limit is 10; split it into messages of 10 or fewer" in said
    assert sent(seat) == []
    send(home, seat, "x" * 10)
    assert len(sent(seat)) == 1


def test_over_the_longest_line_is_refused_and_at_it_passes(person):
    home, seat = person
    reads(home, "max_line_chars = 5\n")
    said = send(home, seat, "short\nlonger\nlonger", ok=False)
    assert "reading: line 2 is 6 chars (and 1 more), ada's limit is 5; break it into shorter lines" in said
    send(home, seat, "short\nfive!")
    assert len(sent(seat)) == 1


def test_one_question_too_many_is_refused_and_at_the_limit_it_passes(person):
    home, seat = person
    reads(home, "max_questions = 1\n")
    said = send(home, seat, "Merge it?\nOr wait?", "--chatter", ok=False)
    assert "reading: 2 questions, ada's limit is 1; send the second as its own message" in said
    assert sent(seat) == []
    send(home, seat, "Merge it?\nThe build is green.", "--chatter")
    assert len(sent(seat)) == 1


def test_every_broken_limit_is_named_in_one_line(person):
    home, seat = person
    reads(home, "max_chars = 5\nmax_questions = 1\n")
    said = send(home, seat, "a?\nb?\nc?", "--chatter", ok=False)
    line = next(l for l in said.splitlines() if "reading:" in l)
    assert "8 chars, ada's limit is 5" in line and "3 questions, ada's limit is 1" in line


def test_the_same_body_again_inside_the_window_is_refused_and_after_it_passes(person):
    home, seat = person
    reads(home, "no_repeat_minutes = 10\n")
    send(home, seat, "The build is green.")
    said = send(home, seat, "The build is green.", ok=False)
    assert "reading: this is the body the channel got 0 min ago, ada's limit is no repeat within 10 min" in said
    send(home, seat, "Merged.")
    assert len(sent(seat)) == 2
    # Only the last letter counts, and only inside the window.
    old = time.time() - 11 * 60
    for path in sent(seat):
        os.utime(path, (old, old))
    send(home, seat, "Merged.")
    assert len(sent(seat)) == 3


def test_a_repeat_already_in_the_ledger_is_refused(person):
    home, seat = person
    reads(home, "no_repeat_minutes = 10\n")
    send(home, seat, "The build is green.")
    # The loom takes it from the port into the ledger, as its sweep does.
    (path,) = sent(seat)
    append(home, fact_from_port(parse_frontmatter(path.read_text()), "s-aaaa-test", 1, seat))
    path.unlink()
    said = send(home, seat, "The build is green.", ok=False)
    assert "no repeat within 10 min" in said


def test_no_file_no_check(person):
    home, seat = person
    send(home, seat, "x" * 5000 + "\n" + "Why?\n" * 3, "--chatter")
    send(home, seat, "x" * 5000 + "\n" + "Why?\n" * 3, "--chatter")
    assert len(sent(seat)) == 2


def test_a_thread_letter_and_a_note_are_never_checked(person):
    home, seat = person
    reads(home, "max_chars = 3\nmax_questions = 1\n")
    letter = inject_letter(home.root, to="thread:work", body="Please")
    command(home, seat, "send", "--to", "thread:other", "--re", letter, "--chatter",
            "far longer than three? and two? questions?\nyes?")
    command(home, seat, "send", "--to", TO, "--re", letter, "--note", "a note far longer than three chars?\nok?")
    assert len(sent(seat)) == 2


def test_a_channel_that_is_not_the_persons_direct_chat_is_not_checked(person):
    home, seat = person
    reads(home, "max_chars = 3\n")
    command(home, seat, "send", "--to", "channel:mine/99", "a group chat is no one's page")
    assert len(sent(seat)) == 1


def test_a_typo_in_the_file_is_refused_not_ignored(person):
    home, seat = person
    reads(home, "max_char = 10\n")
    said = send(home, seat, "hello", ok=False)
    assert "reading: people/ada/reading.toml sets max_char" in said
    reads(home, "max_chars = 0\n")
    assert "max_chars must be a positive whole number" in send(home, seat, "hello", ok=False)


@pytest.mark.parametrize("body", ["", "   \n\t "])
def test_an_empty_body_goes_to_no_channel(person, body):
    home, seat = person
    for to in (TO, "channel:mine/99"):
        said = command(home, seat, "send", "--to", to, body, ok=False)
        assert f"send: the body is empty; nothing goes to {to}" in said
    assert sent(seat) == []
