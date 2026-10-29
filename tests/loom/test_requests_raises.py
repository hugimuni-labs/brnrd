"""A question to a person is an ask, and an unchanged ask is raised once a day (requests R3b): real verbs."""
import os
import time

from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.port import parse_frontmatter
from brr.loom.runtime.raises import questions
from brr.loom.runtime.wishes import wishes
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)

TO = "channel:mine/42"


def person_wish(home, seat, line):
    letter = inject_letter(home.root, to="thread:work", body=line)
    return command(home, seat, "wish", "new", "--line", line, "--say", letter)


def sent(seat, letter_id):
    path = seat / "port" / "out" / f"{letter_id.rsplit('/', 1)[1]}.md"
    return parse_frontmatter(path.read_text())


def asks(home):
    return [w for w in wishes(read_facts(home)).values() if w.sender == "resident"]


def channel(home):
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')


def test_questions_skip_quoted_and_code_lines():
    body = "Done.\nMerge it?\n> did you mean?\n```\nwhy?\n```\n    also?\nok"
    assert questions(body) == ["Merge it?"]


def test_a_question_to_a_person_files_an_ask_under_the_threads_one_wish(ledger):
    home, seat = ledger
    channel(home)
    root = person_wish(home, seat, "Ship the parser")
    letter = command(home, seat, "send", "--to", TO, "Parser is green.\nMerge it now?")
    ask, = asks(home)
    assert (ask.parent, ask.thread, ask.line) == (root, "work", "Merge it now?")
    assert sent(seat, letter)["links"].split() == [ask.id]
    # A letter with no question, or one that opts out, files nothing.
    command(home, seat, "send", "--to", TO, "Parser is green.")
    command(home, seat, "send", "--to", TO, "--chatter", "How was the trip?")
    assert len(asks(home)) == 1


def test_several_open_wishes_and_no_for_is_refused(ledger):
    home, seat = ledger
    channel(home)
    first = person_wish(home, seat, "Ship the parser")
    second = person_wish(home, seat, "Ship the lexer")
    stderr = command(home, seat, "send", "--to", TO, "Which first?", ok=False)
    assert f"--for {first}" in stderr and f"--for {second}" in stderr
    assert asks(home) == []
    command(home, seat, "send", "--to", TO, "--for", second, "Which first?")
    ask, = asks(home)
    assert ask.parent == second


def test_an_unchanged_ask_is_raised_once_a_day_until_the_person_writes(ledger):
    home, seat = ledger
    channel(home)
    person_wish(home, seat, "Ship the parser")
    first = command(home, seat, "send", "--to", TO, "Merge it?")
    ask, = asks(home)
    # Linking the ask is a raise of it, never a second ask.
    stderr = command(home, seat, "send", "--to", TO, f"Still: merge it? &{ask.id}", ok=False)
    assert f"{ask.id} was raised at" in stderr and "nothing moved since" in stderr
    # Over a day ago, it may be raised again.
    path = seat / "port" / "out" / f"{first.rsplit('/', 1)[1]}.md"
    day_ago = time.time() - 25 * 3600
    os.utime(path, (day_ago, day_ago))
    again = command(home, seat, "send", "--to", TO, f"Still: merge it? &{ask.id}")
    assert len(asks(home)) == 1
    # A letter of theirs since the last raise moves it.
    inject_letter(home.root, to="thread:work", body="back now")
    command(home, seat, "send", "--to", TO, f"Merge it? &{ask.id}")
    assert sent(seat, again)["links"].split() == [ask.id]
