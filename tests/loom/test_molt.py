"""A molt ends the body; the next one wakes on the same strand with the letters."""

from brr.loom.runtime.ledger import inject_letter

from _step import Loom, wait_until, write_thread


def test_molt_starts_a_new_body_on_the_same_strand_with_the_owed_letter(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "tm", "thread tm", "molt-once", wait="5")
    letter_id = inject_letter(root, to="thread:tm", body="hold")
    loom = Loom(root, tick=0.05)
    loom.start()
    try:
        found = {}

        def ready():
            facts = loom.facts()
            starts = [fact for fact in facts if fact.kind == "body.started"]
            if len(starts) < 2:
                return False
            wake = root / "rooms" / starts[0].data["strand"] / "port" / "wake-seen"
            if not wake.is_file():
                return False
            found["facts"] = facts
            found["starts"] = starts
            found["wake"] = wake.read_text()
            return True

        wait_until(ready, 8, loom.dump)
        starts = found["starts"]
        assert starts[0].data["strand"] == starts[1].data["strand"]
        assert starts[0].data["gen"] == starts[1].data["gen"]
        assert starts[0].data["pid"] != starts[1].data["pid"]
        assert letter_id in found["wake"]
        assert "hold" in found["wake"]
        kinds = [fact.kind for fact in found["facts"]]
        assert kinds.count("molt") == 1
        assert "body.died" not in kinds
        assert "released" not in kinds
        assert found["facts"][-1].kind != "released"
        molt = next(fact for fact in found["facts"] if fact.kind == "molt")
        assert molt.data["why"] == "fresh body"
        assert molt.data["strand"] == starts[0].data["strand"]
        assert molt.data["gen"] == starts[0].data["gen"]
    finally:
        loom.halt()
        assert not loom.errors
