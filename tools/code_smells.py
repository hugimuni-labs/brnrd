"""A mechanical read of a Python module's shape, for a review to aim with.

Written 2026-09-12 for the daemon review. The point is not that any number
here is a defect — it is that a 20,000-line file cannot be reviewed by
reading it front to back, and guessing where to look is how a review
becomes a sample of the reviewer's priors. This reports *shape*, ranks it,
and leaves the judgement to the reader.

Usage: python3 tools/code_smells.py src/brr/daemon.py [--top 25]
"""
from __future__ import annotations

import argparse
import ast
import collections
import re
import sys
from pathlib import Path

# A comment naming a ticket, so a review can ask whether the lesson outlived
# the fix. Deliberately not resolved against the forge here: that is a network
# call, and this instrument must run offline in any checkout.
TICKET = re.compile(r"#(\d{2,5})\b")
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")


class FuncStat:
    __slots__ = ("name", "lineno", "end", "depth", "branches", "params",
                 "returns", "cls", "docstring", "asserts")

    def __init__(self, name, lineno, end, depth, branches, params, returns,
                 cls, docstring, asserts):
        self.name = name
        self.lineno = lineno
        self.end = end
        self.depth = depth
        self.branches = branches
        self.params = params
        self.returns = returns
        self.cls = cls
        self.docstring = docstring
        self.asserts = asserts

    @property
    def lines(self) -> int:
        return self.end - self.lineno + 1

    @property
    def label(self) -> str:
        return f"{self.cls}.{self.name}" if self.cls else self.name


BRANCH_NODES = (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler,
                ast.With, ast.BoolOp, ast.IfExp, ast.Assert)
NEST_NODES = (ast.If, ast.For, ast.While, ast.Try, ast.With)


def _depth(node: ast.AST, current: int = 0) -> int:
    best = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # a nested def has its own budget
        step = current + 1 if isinstance(child, NEST_NODES) else current
        best = max(best, _depth(child, step))
    return best


def _count_branches(node: ast.AST) -> int:
    total = 0
    for child in ast.walk(node):
        if isinstance(child, BRANCH_NODES):
            total += 1
    return total


def collect(tree: ast.AST) -> list[FuncStat]:
    out: list[FuncStat] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.cls: str | None = None

        def visit_ClassDef(self, node):
            prev, self.cls = self.cls, node.name
            self.generic_visit(node)
            self.cls = prev

        def _func(self, node):
            args = node.args
            params = (len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
                      + bool(args.vararg) + bool(args.kwarg))
            out.append(FuncStat(
                name=node.name, lineno=node.lineno,
                end=getattr(node, "end_lineno", node.lineno),
                depth=_depth(node), branches=_count_branches(node),
                params=params,
                returns=sum(isinstance(n, ast.Return) for n in ast.walk(node)),
                cls=self.cls,
                docstring=ast.get_docstring(node) is not None,
                asserts=sum(isinstance(n, ast.Assert) for n in ast.walk(node)),
            ))
            self.generic_visit(node)

        visit_FunctionDef = _func
        visit_AsyncFunctionDef = _func

    Visitor().visit(tree)
    return out


def literal_strings(tree: ast.AST) -> collections.Counter:
    """String constants used more than once — a fact stored twice is repaired
    once, and a status name repeated in fifteen places is a state machine
    with no vocabulary."""
    counter: collections.Counter = collections.Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if 2 <= len(value) <= 40 and "\n" not in value and " " not in value:
                counter[value] += 1
    return counter


def comment_report(path: Path) -> dict[str, list[tuple[int, str]]]:
    tickets: list[tuple[int, str]] = []
    dates: list[tuple[int, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        if TICKET.search(stripped):
            tickets.append((i, stripped[:120]))
        if DATE.search(stripped):
            dates.append((i, stripped[:120]))
    return {"tickets": tickets, "dates": dates}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--section", default="all",
                    choices=["all", "size", "depth", "branches", "strings", "comments"])
    args = ap.parse_args()

    source = args.path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    stats = collect(tree)
    total_lines = len(source.splitlines())

    print(f"# {args.path} — {total_lines:,} lines, {len(stats)} defs")
    body = sum(s.lines for s in stats)
    print(f"  in a def: {body:,} lines ({body / total_lines:.0%}) · "
          f"module level: {total_lines - body:,}")
    print()

    def table(title, key, fmt, limit=args.top):
        print(f"## {title}")
        for s in sorted(stats, key=key, reverse=True)[:limit]:
            print(f"  {fmt(s):>6}  {s.label:<52} :{s.lineno}-{s.end}")
        print()

    if args.section in ("all", "size"):
        table("Longest defs (lines)", lambda s: s.lines, lambda s: s.lines)
    if args.section in ("all", "depth"):
        table("Deepest nesting", lambda s: (s.depth, s.lines), lambda s: s.depth)
    if args.section in ("all", "branches"):
        table("Most branch points", lambda s: (s.branches, s.lines),
              lambda s: s.branches)
        print("## Widest signatures")
        for s in sorted(stats, key=lambda s: s.params, reverse=True)[:10]:
            print(f"  {s.params:>6}  {s.label:<52} :{s.lineno}")
        print()
    if args.section in ("all", "strings"):
        print("## Repeated bare string literals (a vocabulary with no home)")
        for value, count in literal_strings(tree).most_common(args.top):
            if count >= 4:
                print(f"  {count:>6}  {value!r}")
        print()
    if args.section in ("all", "comments"):
        report = comment_report(args.path)
        print(f"## Comments naming a ticket: {len(report['tickets'])}")
        print(f"## Comments naming a date:   {len(report['dates'])}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
