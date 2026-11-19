"""An empty body goes to no channel (brnrd#2774), through the real ``send`` verb on a temp home.
A --clean letter, whose words are its schema, is not empty: test_labels sends one."""
import pytest

from brr.loom.runtime import audit

from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)


@pytest.fixture
def person(ledger, monkeypatch):
    home, seat = ledger
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    (home.root / "self/people/ada").mkdir(parents=True)
    (home.root / "self/people/ada/channels.toml").write_text('channels = ["mine:42"]\n')
    monkeypatch.delenv(audit.SAY, raising=False)
    return home, seat


def sent(seat):
    return sorted((seat / "port" / "out").glob("*.md"))


@pytest.mark.parametrize("body", ["", "   \n\t "])
def test_an_empty_body_goes_to_no_channel(person, body):
    home, seat = person
    for to in ("channel:mine/42", "channel:mine/99"):
        said = command(home, seat, "send", "--to", to, body, ok=False)
        assert f"send: the body is empty; nothing goes to {to}" in said
    assert sent(seat) == []


def test_a_body_with_words_goes(person):
    home, seat = person
    command(home, seat, "send", "--to", "channel:mine/42", "The build is green.")
    assert len(sent(seat)) == 1
