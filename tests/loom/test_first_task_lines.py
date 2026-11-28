"""A stranger's first task (self threads/first-task): three lines a body reads before it answers."""

from pathlib import Path

from brr.loom.runtime import briefing, library


def test_a_folder_added_with_brnrd_add_says_its_persons_results_are_written_there(tmp_path):
    folder = tmp_path / "Documents" / "old-posts"
    folder.mkdir(parents=True)
    added = library.Entry(name="old-posts", kind="known", whose="dana", path=str(folder),
                          machine=library.machine(), purpose="a project folder, added with brnrd add")
    assert "results for dana are written here" in library.line(added)
    # A folder a body registered for its own reading makes no such promise.
    known = library.Entry(name="refs", kind="known", whose="dana", path=str(folder),
                          machine=library.machine(), purpose="reference papers")
    assert "results for" not in library.line(known)


def test_the_briefing_puts_a_persons_file_where_they_can_open_it():
    assert "A file the person asked for goes in their folder from `lib list`, never only in the self." in briefing.BRIEFING


def test_a_refused_self_landing_is_never_a_persons_ask():
    assert "A self landing the guard refuses stays a note, never a person's ask." in briefing.BRIEFING


def test_the_briefing_offers_only_the_hands_this_installation_has():
    bullet = next(line for line in briefing.BRIEFING.splitlines() if line.startswith("- Offer only hands"))
    assert "no mail, calendar or live documents" in bullet
    assert "never tell a person to connect one" in bullet
    # The bundled connectors are chat doors only; a mail or calendar one would make the line false.
    bundled = Path(briefing.__file__).parent / "connectors"
    assert sorted(p.name for p in bundled.iterdir() if p.is_dir()) == ["fake", "relay", "telegram", "web"]


#: main f3c07534's briefing, in bytes. Every wake pays for each one (test_wishes'
#: row-cap wake had under 60 to spare): a new sentence pays for itself here, by name.
BRIEFING_CEILING = 5037


def test_the_briefing_costs_no_more_than_it_did():
    assert len(briefing.BRIEFING.encode("utf-8")) <= BRIEFING_CEILING
