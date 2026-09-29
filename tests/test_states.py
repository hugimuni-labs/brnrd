import json
from pathlib import Path

from brr import states


REPO_ROOT = Path(__file__).parents[1]


def test_spec_loads_and_real_tree_checks_clean():
    document = states.load()
    assert set(document["machines"]) == {"seat", "strand", "await"}
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
