"""The people fact: a count and a date the jack shows, never a stop of its own (people-pages)."""

import json
import os
import subprocess
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime import people
from brr.loom.runtime.jack import execute
from brr.loom.runtime.port import parse_boundary, render_boundary

STRAND = "s-ab12-aaaaaa"
PAGE = "# arseni\n\n## never\n\n- delete under a home root [1]\n\n## between us\n\n- 11-03: a line [2]\n"


def _git(cwd: Path, *args: str, date: str = "2026-11-03T10:00:00") -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd, env=env, check=True,
                   capture_output=True)


def _room(tmp_path: Path, page: str | None = PAGE) -> Path:
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    folder = room / "self" / "people" / "arseni"
    folder.mkdir(parents=True)
    (folder / "agreement.md").write_text("# Agreement\n")
    if page is not None:
        (folder / "page.md").write_text(page)
    _git(room / "self", "init", "-q", "-b", "main")
    _git(room / "self", "add", "-A")
    _git(room / "self", "commit", "-qm", "seed")
    return room


def _letter(ident: str, sender: str = "person:arseni", body: str = "hi") -> Fact:
    return Fact(kind="letter", by="loom:ab12", id=ident,
                data={"id": ident, "to": "thread:ta", "body": body, "from": sender})


def _write(room: Path, letters, shown=()) -> None:
    text = render_boundary(STRAND, 1, "ta", letters, set(shown), {})
    (room / "port" / "in" / "boundary.md").write_text(text)


def _said(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_tally_counts_a_persons_letters_once_each_and_dates_the_page(tmp_path):
    room = _room(tmp_path)
    parsed = parse_boundary(render_boundary(STRAND, 4, "ta", [_letter("a/1"), _letter("s/2", "s-x")], set(), {}))
    lines, key, kept = people.tally(room / "self", parsed, {})
    assert lines == ["people: 1 letter from arseni this gen; people/arseni/page.md last changed 11-03"]
    assert key == ["arseni 11-03"]
    again = parse_boundary(render_boundary(STRAND, 4, "ta", [_letter("a/1"), _letter("a/3")], set(), {}))
    lines, key, kept = people.tally(room / "self", again, kept)
    assert lines[0].startswith("people: 2 letters from arseni this gen;")
    assert key == ["arseni 11-03"]
    # A new gen counts from zero.
    fresh = parse_boundary(render_boundary(STRAND, 5, "ta", [_letter("a/9")], set(), {}))
    assert people.tally(room / "self", fresh, kept)[0][0].startswith("people: 1 letter from arseni")


def test_a_commit_moves_the_date_and_a_fat_between_us_is_said(tmp_path):
    room = _room(tmp_path)
    parsed = parse_boundary(render_boundary(STRAND, 1, "ta", [_letter("a/1")], set(), {}))
    _, _, kept = people.tally(room / "self", parsed, {})
    page = room / "self" / "people" / "arseni" / "page.md"
    page.write_text(PAGE + "".join(f"- 11-04: line {i} " + "x" * 80 + "\n" for i in range(20)))
    _git(room / "self", "commit", "-qam", "grow", date="2026-11-04T10:00:00")
    lines, key, _ = people.tally(room / "self", parsed, kept)
    assert lines[0].endswith("last changed 11-04")
    assert lines[1].startswith("people: people/arseni/page.md between us is ")
    assert lines[1].endswith(f"bytes, over its {people.BETWEEN_US_BUDGET}")


def test_no_page_yet_is_never_and_a_stranger_is_nobody(tmp_path):
    room = _room(tmp_path, page=None)
    parsed = parse_boundary(render_boundary(
        STRAND, 1, "ta", [_letter("a/1"), _letter("z/1", "person:zed")], set(), {}))
    lines, _, _ = people.tally(room / "self", parsed, {})
    assert lines == ["people: 1 letter from arseni this gen; people/arseni/page.md last changed never"]
    assert people.person_of("p-arseni") == "arseni"
    assert people.person_of("person:../x") == ""
    assert people.person_of("s-ab12-bbbbbb") == ""


def test_the_jack_says_it_at_a_post_once_and_carries_it_in_a_stop_that_blocks(tmp_path):
    room = _room(tmp_path)
    first = _letter("p-arseni/a1", body="first-letter")
    _write(room, [first])
    start = execute("start", room, "{}")
    assert "people:" not in start  # the wake already rendered the page
    _write(room, [first], shown={"p-arseni/a1"})
    said = _said(execute("post", room, json.dumps({"tool_name": "Bash"})))
    assert "people: 1 letter from arseni this gen; people/arseni/page.md last changed 11-03" in said
    # Said once: the same person and date are not news again.
    assert "people:" not in _said(execute("post", room, json.dumps({"tool_name": "Bash"})))
    # A second letter is news; the count moving is not.
    second = _letter("p-arseni/a2", body="second-letter")
    _write(room, [first, second], shown={"p-arseni/a1"})
    said = _said(execute("post", room, json.dumps({"tool_name": "Bash"})))
    assert "second-letter" in said and "people:" not in said
    # The stop blocks on the owed letters anyway, and carries the fact with the new count.
    reason = json.loads(execute("stop", room, "{}"))["reason"]
    assert reason.endswith("people: 2 letters from arseni this gen; people/arseni/page.md last changed 11-03")


def test_the_fact_alone_never_blocks_a_stop(tmp_path):
    room = _room(tmp_path)
    _write(room, [])
    state = room / "port" / "jack-state.json"
    state.write_text(json.dumps({"people": {"gen": 1, "seen": {"arseni": ["p-arseni/a1"]}, "dates": {}}}))
    (room / "port" / "wait").write_text("1\n")  # the stop's poll, bounded as test_jack's is
    assert "people:" not in execute("stop", room, "{}")
