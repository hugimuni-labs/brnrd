"""A landed Plan row that is done grants the wishes it cites; delivered stays declared."""
from brr.loom.runtime.lifecycle import parse_plan
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.merge import send_to_self
from brr.loom.runtime.wishes import Wish, landed_results, progress, receipt, wishes
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
    land(seat, f"- [x] Hold the board: [design](design.md) · re: {built}\n- Overlay, not started · re: {later}")
    tick_once(home, {}, "fake", "haiku")
    found = wishes(read_facts(home))
    assert found[built].state == "granting" and found[later].state == "heard"
    assert [(r["kind"], r["ref"], r["delivered"]) for r in found[built].results] == [
        ("file", "threads/work/design.md", False)]
    # Granting is not done: the plan counts delivered and accepted only.
    assert progress(found, "work")["done"] == 0
    land(seat, f"- [x] Hold the board: [design](design.md) · re: {built}\n"
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
    land(seat, f"- Plan it, w-abc and 4988 · re: {plain}\n- [ ] Follow-up on #2402, see 1a2b3c4d · re: {plain}\n"
               f"- #2392 · re: {plain}\n- Built 1a2b3c4d · re: {dropped}")
    tick_once(home, {}, "fake", "haiku")
    found = wishes(read_facts(home))
    assert found[plain].state == "heard" and found[dropped].state == "dropped"
    assert not found[dropped].results


def test_receipt_kinds():
    # A link to a file of the self is a mention; under [x] it names what the done row made.
    assert receipt("t", "- [notes](research/a.md) · re: w-1") is None
    assert receipt("t", "- [notes](research/a.md) · re: w-1", True) == ("file", "threads/t/research/a.md")
    assert receipt("t", "[x](../other/b.md#head)", True) == ("file", "threads/other/b.md")
    assert receipt("t", "Read it all · re: w-1", True) == ("box", "threads/t/README.md")
    assert receipt("t", "PR #2404 open") == ("pr", "#2404")
    assert receipt("t", "landed as brnrd#2404") == ("pr", "#2404")
    assert receipt("t", "see [design](design.md), landed 585f1321") == ("commit", "585f1321")
    assert receipt("t", "[#7](https://github.com/o/r/pull/7)") == ("pr", "https://github.com/o/r/pull/7")
    assert receipt("t", "PR #2404 open", False) is None
    # A bare #n is context, an issue or a label row, never a receipt.
    assert receipt("inbox", "#2392 · re: w-abc") is None
    assert receipt("t", "(C2) Kept-room follow-up on #2402") is None
    assert receipt("t", "landed 585f1321") == ("commit", "585f1321")
    assert receipt("t", "Done · re: w-abc1234def") is None


# threads/product-shape/README.md's Plan as it landed (self 97b6a19): rows 6 and 7 link
# design.md and say they wait; rows 1 and 4 are ticked and link nothing.
PRODUCT_SHAPE = """## Plan
- [x] Read 4844 whole, README, MANIFESTO, kb/design, loom-onboard, crash-only/design, w-t2r2hyb679x6 (4529) · re: s-ae66-jmqbnt/3g57s, w-effymd115rau
- [x] Research notes: [research/own-reads.md](research/own-reads.md) (file:line), [research/market.md](research/market.md) (sourced scan); code-map delegates stopped at 1.4M spend, covered by own reads · re: s-ae66-jmqbnt/3g57s, w-effymd115rau
- [x] [design.md](design.md) with a ranked head ≤10 lines; report path + 5 lines to thread:inbox · re: s-ae66-jmqbnt/3g57s, w-effymd115rau
- [x] Market delegate's report folded into design.md (fork 0, update channels, trust defaults, phone stop); report to inbox s-ae66-5zy9vm/nevze · re: s-ae66-5zy9vm/wh7pm, w-effymd115rau
- Re-ranked against f3ca5f32 ([design.md](design.md) §5), machines section §6, next three to inbox · re: s-ae66-jtolqi/kw4uv, w-effymd115rau
- Installer sets the channel: `brnrd install` = stable release, `--dev` = checkout + `update = dev`, `brnrd update --channel` switches; auto → stable off a checkout; dev-setup docs ([design.md](design.md) §7). Waits on move 1 and move 3 · re: s-ae66-gn99vk/7wkk1, w-effymd115rau
- Sasha's stable install as canary: `update.outcome` fact, one comment per attempt on a pinned "canary: stable" issue, read by the tending sweep ([design.md](design.md) §7). Waits on move 1 and move 3 · re: s-ae66-gn99vk/7wkk1, w-effymd115rau
"""


def test_product_shape_rows_grant_only_when_done():
    """Each real row alone through landed_results: the ticked ones grant, the waiting ones do not.
    Row 5 names f3ca5f32, a sha, so it is a receipt whatever its box says."""
    found = {"w-effymd115rau": Wish("w-effymd115rau", thread="product-shape", line="A direction")}
    granted = []
    for number, row in enumerate(parse_plan(PRODUCT_SHAPE), 1):
        facts = landed_results(found, {"product-shape": {"plan_rows": [row]}}, "loom")
        granted += [(number, f.data["kind"], f.data["ref"]) for f in facts]
    assert granted == [
        (1, "box", "threads/product-shape/README.md"),
        (2, "file", "threads/product-shape/research/own-reads.md"),
        (3, "file", "threads/product-shape/design.md"),
        (4, "box", "threads/product-shape/README.md"),
        (5, "commit", "f3ca5f32"),
    ]
