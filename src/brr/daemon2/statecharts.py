"""Data-backed transition checks for the replacement machines."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True)
class Machine:
    start: str
    states: frozenset[str]
    edges: frozenset[tuple[str, str, str]]

    def next(self, state: str, trigger: str) -> str:
        choices = {to for source, event, to in self.edges
                   if source == state and event == trigger}
        if len(choices) != 1:
            raise ValueError(f"illegal transition: {state} / {trigger}")
        return choices.pop()

    def check(self) -> None:
        if self.start not in self.states:
            raise ValueError("unknown start state")
        if any(source not in self.states or to not in self.states
               for source, _, to in self.edges):
            raise ValueError("transition names unknown state")
        reached = {self.start}
        while True:
            more = reached | {to for source, _, to in self.edges if source in reached}
            if more == reached:
                break
            reached = more
        if reached != self.states:
            raise ValueError(f"unreachable states: {self.states - reached}")


def load(name: str) -> Machine:
    if name not in {"seat", "letter", "ask"}:
        raise ValueError("unknown machine")
    path = resources.files("brr.daemon2").joinpath("states", name + ".yaml")
    raw = json.loads(path.read_text(encoding="utf-8"))
    machine = Machine(raw["start"], frozenset(raw["states"]),
                      frozenset((row["from"], row["on"], row["to"])
                                for row in raw["transitions"]))
    machine.check()
    return machine
