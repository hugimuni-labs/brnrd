"""Two deaths inside ten minutes stop the respawns."""

import time

from brr.loom.runtime.ledger import inject_letter

from _step import Loom, wait_until, write_thread


def test_two_immediate_deaths_fuse_the_strand(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "td", "thread td", "die-now", wait="5")
    inject_letter(root, to="thread:td", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    try:
        def fused():
            return any(fact.id.startswith("attention:fuse:") for fact in loom.facts())

        wait_until(fused, 5, loom.dump)
        # A third launch would land on a later tick. Give it several.
        time.sleep(0.4)
        facts = loom.facts()
        starts = [fact for fact in facts if fact.kind == "body.started"]
        deaths = [fact for fact in facts if fact.kind == "body.died"]
        assert len(starts) == 2
        assert len(deaths) == 2
        assert len({fact.data["strand"] for fact in starts}) == 1
        attentions = [fact for fact in facts if fact.kind == "attention"]
        assert len(attentions) == 1
        assert attentions[0].id == f"attention:fuse:{starts[0].data['strand']}"
    finally:
        loom.halt()
        assert not loom.errors
