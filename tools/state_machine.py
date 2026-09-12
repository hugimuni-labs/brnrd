"""Extract the run state machine as the code actually implements it.

Every write to a `Run.status` — direct assignment or `update_status(...)` —
reported with the function it happens in and the literal it writes. The
point is the delta between this and the declared `run.STATUSES`: a state
machine whose vocabulary is documentation rather than a constraint will
grow a state nobody listed, and the listing is where a reader goes first.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def enclosing(tree: ast.AST) -> dict[int, str]:
    """line number -> the def it is inside (innermost wins)."""
    owner: dict[int, str] = {}

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                new = stack + [child.name]
                for line in range(child.lineno, getattr(child, "end_lineno", child.lineno) + 1):
                    owner[line] = new[-1]
                walk(child, new)
            else:
                walk(child, stack)

    walk(tree, [])
    return owner


def literal(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.IfExp):
        return f"{literal(node.body)} if … else {literal(node.orelse)}"
    if isinstance(node, ast.Attribute):
        return f"{ast.unparse(node)}"
    try:
        return ast.unparse(node)[:60]
    except Exception:
        return "<expr>"


def main() -> int:
    path = Path(sys.argv[1])
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = enclosing(tree)
    rows: list[tuple[int, str, str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "status":
                    rows.append((node.lineno, owner.get(node.lineno, "<module>"),
                                 f"{ast.unparse(target)} =", literal(node.value)))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "update_status":
                arg = literal(node.args[0]) if node.args else "?"
                rows.append((node.lineno, owner.get(node.lineno, "<module>"),
                             f"{ast.unparse(func)}()", arg))

    rows.sort()
    print(f"# {path} — {len(rows)} run-status writes\n")
    width = max((len(r[1]) for r in rows), default=10)
    for line, fn, how, value in rows:
        print(f"  :{line:<6} {fn:<{width}}  {how:<28} {value}")

    print("\n## Distinct literals written")
    seen: dict[str, int] = {}
    for _, _, _, value in rows:
        seen[value] = seen.get(value, 0) + 1
    for value, count in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>3}  {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
