"""The registry of facts and the command declaration, held against the table."""

import ast
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import bodies, loom, registry
from brr.loom.runtime.bodies import COMMANDS, OBSERVED, TABLE, Row, Step
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.registry import CLOSED, KINDS, REGISTRY, UndeclaredFact, problems

STATES = {"wanted", "parked", "running", "leaving", "stopping", "unidentified", "exited", "ended"}


def _body(kind, by="loom:aaaa", **data):
    return Fact(kind=kind, by=by, data={"strand": "s-aaaa-000001", "gen": 1, **data})


@pytest.mark.parametrize("fact, why", [
    (_body("body.invented"), "kind body.invented is not declared"),
    (_body("body"), "kind body is not declared"),
    (_body("body.exited", by="strand:s-aaaa-000001", code=0), "written by loom, not strand"),
    (_body("body.exited"), "body.exited lacks code"),
    (_body("body.stayed", pid=1), "body.stayed does not declare pid"),
])
def test_the_write_path_refuses_what_the_registry_does_not_declare(tmp_path, fact, why):
    home = Home(tmp_path)
    # loom._record is what the executor's hands call; it ends in ledger.append.
    with pytest.raises(UndeclaredFact, match=why):
        loom._record(home, fact.kind, fact.data, fact.id) if fact.by.startswith("loom:") else append(home, fact)
    assert read_facts(home) == []


def test_a_declared_body_fact_and_an_undeclared_kind_of_an_open_resource_are_written(tmp_path):
    home = Home(tmp_path)
    append(home, _body("body.exited", code=None))
    append(home, _body("body.exited", code=0, why="spawn failed"))
    # port is not closed: the slice declares port.drained and guards nothing beside it.
    append(home, _body("port.invented", anything=1))
    assert [f.kind for f in read_facts(home)] == ["body.exited", "body.exited", "port.invented"]


def _kinds_read(module) -> set[str]:
    """Kinds the pure module compares ``.kind`` with or asks ``fact_for`` for."""
    def strings(node):
        return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute)
                and node.left.attr == "kind"):
            for comparator in node.comparators:
                found |= strings(comparator)
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "fact_for"
                and len(node.args) > 1):
            found |= strings(node.args[1])
    return found


def walk(table=TABLE, commands=COMMANDS, observed=OBSERVED, hands=loom.HANDS, kinds=KINDS) -> list[str]:
    """Everything the table, the commands, the hands and the registry disagree on."""
    wrong = []
    used_commands, used_observed = set(), set()
    for i, row in enumerate(table):
        for state in (*row.states, row.next):
            if state not in STATES:
                wrong.append(f"row {i}: state {state} is not a state")
        if row.observed not in observed:
            wrong.append(f"row {i}: observation {row.observed!r} is not declared")
        used_observed.add(row.observed)
        for name in row.commands:
            used_commands.add(name)
            if name not in commands:
                wrong.append(f"row {i}: command {name} is not declared")
            # A row whose next state is one of its own can fire again unchanged.
            elif row.next in row.states and not commands[name].repeat:
                wrong.append(f"row {i}: {name} is not safe to repeat on a row that returns to its own state")
    wrong += [f"command {name} is on no row" for name in sorted(set(commands) - used_commands)]
    wrong += [f"observation {name!r} is on no row" for name in sorted(set(observed) - used_observed)]
    for name, step in commands.items():
        if step.hand not in hands:
            wrong.append(f"command {name}: no hand {step.hand}")
        if step.records is not None and step.records not in kinds:
            wrong.append(f"command {name}: records undeclared {step.records}")
        elif step.hand == "record" and kinds[step.records].resource not in CLOSED:
            wrong.append(f"command {name}: the record hand writes only a closed resource")
    wrong += [f"hand {name} performs no command" for name in sorted(set(hands) - {s.hand for s in commands.values()})]
    recorded = {step.records for step in commands.values()}
    wrong += [f"{row.kind} is written by no command" for row in kinds.values()
              if row.resource in CLOSED and row.kind not in recorded]
    wrong += [f"the table reads undeclared {kind}" for kind in sorted(_kinds_read(bodies) - set(kinds))]
    return wrong


def test_the_table_names_only_declared_commands_observations_hands_and_facts():
    assert walk() == []
    assert _kinds_read(bodies) >= {"lease", "shown", "released", "attempt.ended", "body.stopped", "molt"}


@pytest.mark.parametrize("change, said", [
    (dict(table=TABLE + (Row(("running",), "alive, open", "leaving", ("vanish",)),)), "command vanish is not declared"),
    (dict(table=TABLE + (Row(("running",), "on fire", "ended", ()),)), "observation 'on fire' is not declared"),
    (dict(table=TABLE + (Row(("limbo",), "any", "ended", ()),)), "state limbo is not a state"),
    (dict(table=TABLE + (Row(("stopping",), "alive, proven", "stopping", ("spawn",)),)), "spawn is not safe to repeat"),
    (dict(commands={**COMMANDS, "stayed": Step("stayed", "record", "body.cheered", True)}), "records undeclared body.cheered"),
    (dict(commands={**COMMANDS, "signal": Step("signal", "wave", None, True)}), "no hand wave"),
    (dict(commands={**COMMANDS, "drained": Step("drained", "record", "port.drained", True)}), "writes only a closed resource"),
    (dict(kinds={k: v for k, v in KINDS.items() if k != "lease"}), "the table reads undeclared lease"),
    (dict(kinds={**KINDS, "body.spare": replace(KINDS["body.stayed"], kind="body.spare")}), "body.spare is written by no command"),
])
def test_the_walk_fails_on_each_kind_of_undeclared_name(change, said):
    assert any(said in line for line in walk(**change)), walk(**change)


def test_the_registry_is_well_formed():
    assert len(KINDS) == len(REGISTRY)
    for row in REGISTRY:
        assert set(row.writer) <= set(registry.ROLES) and row.writer, row
        assert row.scope in registry.SCOPES, row
        assert len(set(row.names())) == len(row.fields), row
        # The body table folds by strand and generation.
        assert {"strand", "gen"} <= set(row.required()), row


def test_a_live_run_writes_only_facts_the_registry_accepts(tmp_path, capsys):
    """Start, leave, stop, collect through ``run()``; the child's facts are printed."""
    from _step import Loom, kill_bodies, wait_until
    from test_stop_settled_body import _child_pid, _home, _ready

    root = _home(tmp_path)
    live = Loom(root)
    live.start()
    try:
        wait_until(lambda: _ready(root, live.facts()), 12, live.dump)
        pid = _child_pid(root)
        (root / "go").write_text("go")
        strand = next(f.data["strand"] for f in live.facts()
                      if f.kind == "body.started" and f.data["pid"] == pid)
        wait_until(lambda: any(f.kind == "released" and f.data["strand"] == strand for f in live.facts()),
                   12, live.dump)
    finally:
        live.halt()
        kill_bodies(live)
    assert not live.errors
    facts = live.facts()
    mine = [f for f in facts if f.data.get("strand") == strand and f.kind in KINDS]
    with capsys.disabled():
        print()
        for f in mine:
            data = {k: v for k, v in f.data.items() if k not in {"strand", "gen", "dispositions", "evidence", "repos"}}
            print(f"{f.kind:18} {f.by:10} {data}")
    assert [f.kind for f in mine if KINDS[f.kind].resource != "letter"] == [
        "lease", "body.started", "body.leaving", "body.stopped", "body.exited",
        "attempt.ended", "port.drained", "released"]
    # Every declared kind in the whole ledger, the opener's and the open resources' too.
    assert [(f.id, problems(f)) for f in facts if f.kind in KINDS and problems(f)] == []
    assert {f.kind for f in facts if f.kind.startswith("body.")} <= set(KINDS)
