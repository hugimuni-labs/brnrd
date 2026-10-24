"""A landed Plan row with a receipt grants the wishes it cites; delivered stays declared."""
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.merge import send_to_self
from brr.loom.runtime.wishes import progress, receipt, wishes
from test_wake import commit
from test_wishes import heard, ledger  # noqa: F401  (fixture)


def land(seat, plan):
    clone = seat / "self"
    readme = clone / "threads/work/README.md"
    text = readme.read_text()
    readme.write_text(text.split("## Plan")[0] + "## Plan\n\n" + plan + "\n\n## Notes\nKeep notes\n")
    (readme.parent / "design.md").write_text("# Design\n")
    commit(clone, "plan")
    outcome = send_to_self(clone)
    assert outcome.status == "merged", outcome.stderr


def test_a_receipted_row_grants_its_wish_at_the_next_tick(ledger):
    home, seat = ledger
    built = heard(home, seat, "Board holds still")
    later = heard(home, seat, "Overlay opens")
    land(seat, f"- Hold the board: [design](design.md) · re: {built}\n- Overlay, not started · re: {later}")
    tick_once(home, {}, "fake", "haiku")
    found = wishes(read_facts(home))
    assert found[built].state == "granting" and found[later].state == "heard"
    assert [(r["kind"], r["ref"], r["delivered"]) for r in found[built].results] == [
        ("file", "threads/work/design.md", False)]
    # Granting is not done: the plan counts delivered and accepted only.
    assert progress(found, "work")["done"] == 0
    land(seat, f"- Hold the board: [design](design.md) · re: {built}\n"
               f"- Overlay: https://github.com/o/r/pull/7 · re: {later}, {built}")
    tick_once(home, {}, "fake", "haiku")
    tick_once(home, {}, "fake", "haiku")
    found = wishes(read_facts(home))
    assert found[later].state == "granting"
    assert [r["ref"] for r in found[built].results] == ["threads/work/design.md", "https://github.com/o/r/pull/7"]


def test_a_row_without_a_receipt_or_a_closed_wish_grants_nothing(ledger):
    home, seat = ledger
    from test_wishes import command
    plain = heard(home, seat, "Plain")
    dropped = heard(home, seat, "Dropped")
    command(home, seat, "wish", dropped, "--drop", "Not wanted")
    land(seat, f"- Plan it, w-abc and 4988 · re: {plain}\n- Built 1a2b3c4d · re: {dropped}")
    tick_once(home, {}, "fake", "haiku")
    found = wishes(read_facts(home))
    assert found[plain].state == "heard" and found[dropped].state == "dropped"
    assert not found[dropped].results


def test_receipt_kinds():
    assert receipt("t", "- [notes](research/a.md) · re: w-1") == ("file", "threads/t/research/a.md")
    assert receipt("t", "[x](../other/b.md#head)") == ("file", "threads/other/b.md")
    assert receipt("t", "PR #2404 open") == ("pr", "#2404")
    assert receipt("t", "landed 585f1321") == ("commit", "585f1321")
    assert receipt("t", "Done · re: w-abc1234def") is None
