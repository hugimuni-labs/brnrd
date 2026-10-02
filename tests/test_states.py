import json
from pathlib import Path

from brr import states


REPO_ROOT = Path(__file__).parents[1]


def test_spec_loads_and_real_tree_checks_clean():
    document = states.load()
    assert set(document["machines"]) == {"seat", "strand", "await", "event"}
    assert states.check(REPO_ROOT) == []


def test_check_reports_dropped_transition_and_renamed_symbol(tmp_path):
    copied = tmp_path / "repo"
    spec_path = copied / "src/brr/states/seat.yaml"
    spec_path.parent.mkdir(parents=True)
    document = states.load()
    seat = document["machines"]["seat"]
    seat["transitions"] = [
        row for row in seat["transitions"]
        if not (row["from"] == "held_operator" and row["trigger"] == "release: operator")
    ]
    seat["transitions"][0]["code"] = "src/brr/daemon.py::not_a_real_symbol"
    spec_path.write_text(json.dumps(document), encoding="utf-8")
    for relative in ("src/brr/daemon.py", "src/brr/resource_hold.py", "src/brr/await_verb.py", "src/brr/cli.py"):
        target = copied / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO_ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")

    problems = states.check(copied)

    assert any("held_operator" in problem and "release transition" in problem for problem in problems)
    assert any("not_a_real_symbol" in problem for problem in problems)


def test_mermaid_contains_every_state_id():
    document = states.load()
    for name, machine in document["machines"].items():
        rendered = states.render_mermaid(name)
        assert rendered.startswith("flowchart LR\n")
        for state in machine["states"]:
            assert state["id"] in rendered


def test_check_reports_an_event_status_the_spec_does_not_name(tmp_path):
    # The event machine is held to its writers: a new literal handed to
    # set_status anywhere in the package fails the check, and so does a
    # LETTER_STATUSES member the spec forgot.
    copied = tmp_path / "repo"
    spec_path = copied / "src/brr/states/seat.yaml"
    spec_path.parent.mkdir(parents=True)
    document = states.load()
    event = document["machines"]["event"]
    event["states"] = [row for row in event["states"] if row["id"] != "noted"]
    event["transitions"] = [row for row in event["transitions"] if row["to"] != "noted"]
    spec_path.write_text(json.dumps(document), encoding="utf-8")
    for relative in (
        "src/brr/daemon.py", "src/brr/resource_hold.py", "src/brr/await_verb.py",
        "src/brr/cli.py", "src/brr/protocol.py", "src/brr/init_wake.py",
        "src/brr/gates/runtime.py",
    ):
        target = copied / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO_ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    (copied / "src/brr/newwriter.py").write_text(
        'def f(protocol, ev):\n    protocol.set_status(ev, "archived")\n', encoding="utf-8",
    )

    problems = states.check(copied)

    assert "event: code writes status 'archived' but the spec does not name it" in problems
    assert "event: code writes status 'noted' but the spec does not name it" in problems
    assert "event: protocol.LETTER_STATUSES has 'noted' but the spec does not name it" in problems
