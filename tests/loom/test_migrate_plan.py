"""migrate-plan moves what Plan rows carry onto their wishes, once, and never over a body's left."""
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.wishes import wish_tree, wishes
from test_landed_grants import land
from test_wishes import command, heard, ledger  # noqa: F401  (fixture)


def test_rows_become_results_says_and_lefts_once(ledger):
    home, seat = ledger
    own = heard(home, seat, "Board holds still")
    lefted = heard(home, seat, "Overlay opens")
    command(home, seat, "wish", lefted, "--left", "Draw the overlay")
    done = heard(home, seat, "Done already")
    command(home, seat, "wish", done, "--result", "pr:https://example.com/1", "--line", "Shipped", "--deliver")
    elsewhere = command(home, seat, "wish", "new", "--line", "Other work", "--thread", "other",
                        "--say", inject_letter(home.root, to="thread:other", body="Please: other"))
    steer = inject_letter(home.root, to="thread:work", body="Keep it still on phones too")
    land(seat, f"- [x] Built 1a2b3c4d · re: {own}\n"
               f"- Overlay, not started · re: {own}, {steer}\n"
               f"- [ ] Polish  the   edges · re: {own}, {lefted}, {done}\n"
               f"- Ask other about it · re: {elsewhere}")
    before = wishes(read_facts(home))

    dry = command(home, seat, "migrate-plan", person=True)
    assert dry.endswith("would write: 1 results, 1 says, 1 lefts · rerun with --write")
    assert wishes(read_facts(home))[own].results == [] and wishes(read_facts(home))[own].left is None

    command(home, seat, "migrate-plan", "--write", person=True)
    found = wishes(read_facts(home))
    # The receipted row is a result; the two without one are the left, in row order.
    assert [(r["kind"], r["ref"]) for r in found[own].results] == [("commit", "1a2b3c4d")]
    assert found[own].left["steps"] == ["Overlay, not started (from README)", "Polish the edges (from README)"]
    assert (found[own].left["by"], found[own].left["gen"]) == ("loom:migrate", 0)
    # A body's left is never written over; a delivered wish and another thread's wish get none.
    assert found[lefted].left["steps"] == ["Draw the overlay"] and len(found[lefted].lefts) == 1
    assert found[done].left is None and found[elsewhere].left is None
    # The cited person letter is now said, dated at the letter: no new steer, no younger age.
    letter_at = next(f.at for f in read_facts(home) if f.id == steer)
    stamp = next(s for s in found[own].stamps if s["letters"] == [steer])
    assert stamp["at"] == letter_at and stamp["by"] == "loom:migrate"
    assert found[own].at >= before[own].at
    assert not any(line.startswith("  steered since") for line in wish_tree(found[own]))

    again = command(home, seat, "migrate-plan", "--write", person=True)
    assert again == "wrote: 0 results, 0 says, 0 lefts"
