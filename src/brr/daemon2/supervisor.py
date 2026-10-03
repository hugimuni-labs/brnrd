"""Strands belong to an ask and an exact parent edge, not a repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .facts import FactStore
from .seat import Signal


@dataclass(frozen=True)
class Child:
    ask: str
    conversation: str
    parent: str
    edge: str
    run: str
    status: str
    report: str | None = None
    branch: str | None = None


class Supervisor:
    def __init__(self, facts: FactStore):
        self.facts = facts

    def children(self, ask: str) -> dict[str, Child]:
        result: dict[str, Child] = {}
        for fact in self.facts.read("asks", ask):
            data = fact.data
            if fact.kind == "child_started":
                result[data["edge"]] = Child(ask, data["conversation"],
                                             data["parent"], data["edge"],
                                             data["run"], "running")
            elif fact.kind == "child_returned":
                prior = result.get(data["edge"])
                if prior and prior.run == data["run"]:
                    result[data["edge"]] = Child(
                        ask, prior.conversation, prior.parent, prior.edge,
                        prior.run, "returned", data.get("report"), data.get("branch"))
        return result

    def register(self, ask: str, conversation: str, parent: str,
                 edge: str, run: str) -> Child:
        if not all((ask, conversation, parent, edge, run)):
            raise ValueError("child needs an ask, conversation, parent, edge and run")
        if edge in self.children(ask):
            raise ValueError("child edge already exists")
        self.facts.record("asks", ask, "child_started", parent,
                          {"conversation": conversation, "parent": parent,
                           "edge": edge, "run": run})
        return self.children(ask)[edge]

    def returned(self, ask: str, conversation: str, parent: str,
                 edge: str, run: str, *, report: str | None = None,
                 branch: str | None = None) -> Signal:
        child = self.children(ask).get(edge)
        if child is None or (child.conversation, child.parent, child.run) != (
                conversation, parent, run):
            raise ValueError("foreign or unknown child return")
        if child.status != "returned":
            self.facts.record("asks", ask, "child_returned", run,
                              {"edge": edge, "run": run, "report": report,
                               "branch": branch})
        return Signal("child", conversation, ask=ask, parent=parent, edge=edge)
