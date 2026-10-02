"""Checked seat, strand, and await state-machine artifact."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

_SPEC = Path(__file__).with_name("seat.yaml")
_STATE_FIELDS = {"id", "summary", "alive", "cost_to_stay", "user_exits", "terminal"}
_TRANSITION_FIELDS = {"from", "to", "trigger", "actor", "guard", "cost", "code"}
_ALIVE = {"process", "context", "lease", "hold", "file", "none"}
_STAY_COSTS = {"zero", "per-boundary-warm-read", "tokens-while-thinking"}
_ACTORS = {"user", "daemon", "resident", "provider", "strand"}
_LEAVE_COSTS = {"free", "warm-resume", "cold-boot", "tokens"}
_LINK_COLOURS = {
    "free": "#6b7280",
    "warm-resume": "#38bdf8",
    "cold-boot": "#f97316",
    "tokens": "#facc15",
}


def load(path: str | Path | None = None) -> dict[str, Any]:
    """Load the spec. JSON syntax is used because JSON is a YAML 1.2 subset."""
    return json.loads(Path(path or _SPEC).read_text(encoding="utf-8"))


def render_mermaid(machine: str) -> str:
    """Render *machine* as a Mermaid ``flowchart LR``."""
    spec = load()["machines"][machine]
    lines = ["flowchart LR"]
    for state in spec["states"]:
        # The id leads (it is the name the table, the code and the user say);
        # the summary rides under it. Always quoted: an unquoted `;` or `(`
        # in a summary ends a Mermaid statement.
        label = f"<b>{_mermaid_text(state['id'])}</b><br/>{_mermaid_text(state['summary'])}"
        brackets = ('[["', '"]]') if state["terminal"] else ('["', '"]')
        lines.append(f"  {state['id']}{brackets[0]}{label}{brackets[1]}")
    for transition in spec["transitions"]:
        trigger = _mermaid_text(transition["trigger"])
        lines.append(f"  {transition['from']} -->|{trigger}| {transition['to']}")
    for index, transition in enumerate(spec["transitions"]):
        colour = _LINK_COLOURS[transition["cost"]]
        lines.append(f"  linkStyle {index} stroke:{colour},stroke-width:2px")
    return "\n".join(lines) + "\n"


def _mermaid_text(value: object) -> str:
    return str(value).replace('"', "&quot;").replace("|", "&#124;")


def check(repo_root: str | Path) -> list[str]:
    """Return every structural, source-symbol, and completeness problem."""
    root = Path(repo_root)
    spec_path = root / "src/brr/states/seat.yaml"
    try:
        document = load(spec_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"spec: cannot load {spec_path}: {exc}"]
    problems: list[str] = []
    machines = document.get("machines")
    if not isinstance(machines, dict):
        return ["spec: top-level machines must be a mapping"]
    for name, machine in machines.items():
        problems.extend(_check_machine(str(name), machine, root))
    problems.extend(_check_completeness(machines, root))
    return problems


def _check_machine(name: str, machine: object, root: Path) -> list[str]:
    if not isinstance(machine, dict):
        return [f"{name}: machine must be a mapping"]
    problems: list[str] = []
    states = machine.get("states") or []
    transitions = machine.get("transitions") or []
    state_ids: set[str] = set()
    for index, state in enumerate(states):
        prefix = f"{name}.states[{index}]"
        if not isinstance(state, dict):
            problems.append(f"{prefix}: must be a mapping")
            continue
        if set(state) != _STATE_FIELDS:
            problems.append(f"{prefix}: fields must be exactly {sorted(_STATE_FIELDS)}")
        sid = str(state.get("id") or "")
        if not sid or sid in state_ids:
            problems.append(f"{prefix}: id is empty or duplicated: {sid!r}")
        state_ids.add(sid)
        alive = state.get("alive")
        if not isinstance(alive, list) or not set(alive) <= _ALIVE:
            problems.append(f"{prefix}: alive contains an unknown value")
        if state.get("cost_to_stay") not in _STAY_COSTS:
            problems.append(f"{prefix}: invalid cost_to_stay {state.get('cost_to_stay')!r}")
        if not isinstance(state.get("terminal"), bool):
            problems.append(f"{prefix}: terminal must be bool")
    outgoing: dict[str, list[dict[str, Any]]] = {sid: [] for sid in state_ids}
    for index, transition in enumerate(transitions):
        prefix = f"{name}.transitions[{index}]"
        if not isinstance(transition, dict):
            problems.append(f"{prefix}: must be a mapping")
            continue
        if set(transition) != _TRANSITION_FIELDS:
            problems.append(f"{prefix}: fields must be exactly {sorted(_TRANSITION_FIELDS)}")
        source, target = transition.get("from"), transition.get("to")
        if source not in state_ids:
            problems.append(f"{prefix}: unknown from state {source!r}")
        else:
            outgoing[str(source)].append(transition)
        if target not in state_ids:
            problems.append(f"{prefix}: unknown to state {target!r}")
        if transition.get("actor") not in _ACTORS:
            problems.append(f"{prefix}: invalid actor {transition.get('actor')!r}")
        if transition.get("cost") not in _LEAVE_COSTS:
            problems.append(f"{prefix}: invalid cost {transition.get('cost')!r}")
        problem = _check_code_symbol(root, str(transition.get("code") or ""))
        if problem:
            problems.append(f"{prefix}: {problem}")
    start = str(machine.get("start") or "")
    if start not in state_ids:
        problems.append(f"{name}: unknown start state {start!r}")
    else:
        reachable = {start}
        changed = True
        while changed:
            changed = False
            for transition in transitions:
                if transition.get("from") in reachable and transition.get("to") not in reachable:
                    reachable.add(transition["to"])
                    changed = True
        for sid in sorted(state_ids - reachable):
            problems.append(f"{name}: state {sid!r} is unreachable from {start!r}")
    for state in states:
        if not isinstance(state, dict) or state.get("terminal"):
            continue
        sid = str(state.get("id") or "")
        has_user_edge = any(row.get("actor") == "user" for row in outgoing.get(sid, ()))
        if not has_user_edge and not state.get("user_exits"):
            problems.append(f"{name}: non-terminal state {sid!r} has no user way out")
        if sid.startswith("held") and not any(
            str(row.get("trigger") or "").startswith("release:")
            for row in outgoing.get(sid, ())
        ):
            problems.append(f"{name}: held state {sid!r} names no release transition")
    return problems


def _check_code_symbol(root: Path, reference: str) -> str | None:
    try:
        relative, symbol = reference.split("::", 1)
    except ValueError:
        return f"invalid code reference {reference!r}"
    path = root / relative
    if not path.is_file():
        return f"code file does not exist: {relative}"
    text = path.read_text(encoding="utf-8")
    patterns = (
        rf"(?m)^(?:async\s+)?def\s+{re.escape(symbol)}\s*\(",
        rf"(?m)^class\s+{re.escape(symbol)}\b",
        rf"(?m)^{re.escape(symbol)}(?:\s*:[^=\n]+)?\s*=",
    )
    if not any(re.search(pattern, text) for pattern in patterns):
        return f"code symbol does not exist: {reference}"
    return None


def _check_completeness(machines: dict[str, Any], root: Path) -> list[str]:
    problems: list[str] = []
    all_text = lambda machine: " ".join(
        f"{row.get('trigger', '')} {row.get('guard', '')}"
        for row in machines.get(machine, {}).get("transitions", ())
    )
    seat_text = all_text("seat")
    for kind in sorted(_resume_kinds(root / "src/brr/resource_hold.py")):
        if f"resume: {kind}" not in seat_text and f"release: {kind}" not in seat_text:
            problems.append(f"seat: code accepts resume kind {kind!r} but the spec does not name it")
    await_text = all_text("await")
    for outcome in sorted(_await_outcomes(root)):
        if f"outcome:{outcome}" not in await_text:
            problems.append(f"await: code writes outcome {outcome!r} but the spec does not name it")
    event_states = {
        str(state.get("id"))
        for state in machines.get("event", {}).get("states", ())
        if isinstance(state, dict)
    }
    for status in sorted(_event_statuses_written(root)):
        if status not in event_states:
            problems.append(f"event: code writes status {status!r} but the spec does not name it")
    for status in sorted(_letter_statuses(root / "src/brr/protocol.py")):
        if status not in event_states:
            problems.append(f"event: protocol.LETTER_STATUSES has {status!r} but the spec does not name it")
    return problems


def _event_statuses_written(root: Path) -> set[str]:
    # Every literal an event file's `status:` can receive: the two writers
    # (protocol.set_status and daemon._set_event_status_if_present) and the
    # birth status create_event is given. A new literal anywhere in the
    # package fails the check until the event machine names it.
    pattern = re.compile(
        r'(?:set_status|_set_event_status_if_present)\(\s*[^,()]+,\s*"([a-z_]+)"\s*\)'
        r'|create_event\([^)]*?\bstatus="([a-z_]+)"',
        re.S,
    )
    found: set[str] = set()
    for path in sorted((root / "src/brr").rglob("*.py")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            found.add(match.group(1) or match.group(2))
    return found


def _letter_statuses(path: Path) -> set[str]:
    # Source of truth: protocol.py::LETTER_STATUSES (a frozenset literal).
    if not path.is_file():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "LETTER_STATUSES"
            for target in node.targets
        ):
            call = node.value
            if isinstance(call, ast.Call) and call.args and isinstance(call.args[0], ast.Set):
                return {
                    item.value for item in call.args[0].elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
    return set()


def _resume_kinds(path: Path) -> set[str]:
    # Source of truth: resource_hold.py::RESUME_CONDITIONS and its RESUME_* literals.
    tree = ast.parse(path.read_text(encoding="utf-8"))
    literals: dict[str, str] = {}
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("RESUME_"):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        literals[target.id] = node.value.value
                if isinstance(target, ast.Name) and target.id == "RESUME_CONDITIONS":
                    call = node.value
                    if isinstance(call, ast.Call) and call.args:
                        collection = call.args[0]
                        if isinstance(collection, (ast.Set, ast.List, ast.Tuple)):
                            names.update(
                                item.id for item in collection.elts if isinstance(item, ast.Name)
                            )
    return {literals[name] for name in names if name in literals}


def _await_outcomes(root: Path) -> set[str]:
    # Sources of truth: await_verb.evaluate (event/condition),
    # daemon._resolve_await_state plus park facets (timeout/park), and
    # cli.cmd_await's call-lease return (pending).
    await_source = (root / "src/brr/await_verb.py").read_text(encoding="utf-8")
    daemon_source = (root / "src/brr/daemon.py").read_text(encoding="utf-8")
    cli_source = (root / "src/brr/cli.py").read_text(encoding="utf-8")
    outcomes = set(re.findall(r'return\s+"(event|condition)"\s*,', await_source))
    outcomes.update(re.findall(r'(?:outcome\s*=|\["outcome"\]\s*=)\s*"(timeout|park)"', daemon_source))
    outcomes.update(re.findall(r'_emit\([^\n]+,\s*"(pending)"', cli_source))
    return outcomes
