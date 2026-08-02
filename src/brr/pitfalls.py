"""Trigger-indexed failure-memory — the env-shaping loop's *remember* step.

When the resident hits friction worth remembering but not yet worth a
forcing function, it records a **pitfall** in its dominion
(``pitfalls.md`` in the resident dominion): a lesson keyed by one or more *triggers*
(keywords or loci that tend to appear when the failure is about to
recur). On each wake, brr matches the current task's text against those
triggers and injects the matching pitfalls into the prompt.

This is the **affordance** rung of the robustness hierarchy
(``kb/design-environment-shaping.md``): the failure-memory placed *in the
path* so it can't be silently skipped, rather than prose the agent must
remember to re-read (recall) — but cheaper than a forcing function, so
it's where a lesson lives until it's compiled down to a lint/test/baked
tool and the pitfall is slashed.

Storage is the dominion (owned, durable), superseding the earlier idea of
``Pitfall:`` markers on shared ``kb/`` pages; surfacing is this
deterministic daemon-side matcher, complementing the agent-curated
self-inject digest (self-inject is *always-on* pins; this is *by-trigger*,
scoped to the task at hand). See ``kb/design-agent-dominion.md`` §2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PITFALLS_FILE = "pitfalls.md"
_TRIGGER_RE = re.compile(r"^\s*trigger:\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class Pitfall:
    """One recorded failure-memory: a titled lesson keyed by triggers."""

    title: str
    triggers: list[str] = field(default_factory=list)
    body: str = ""

    def matches(self, text: str) -> bool:
        """True if any trigger is a case-insensitive substring of *text*."""
        low = text.lower()
        return any(t and t.lower() in low for t in self.triggers)


def parse_pitfalls(dominion_dir: Path) -> list[Pitfall]:
    """Parse the dominion's ``pitfalls.md`` into :class:`Pitfall` records.

    Format is deliberately light so it's natural to hand-write: a ``## ``
    heading per pitfall, an optional ``trigger:`` line (comma-separated)
    anywhere in its block, and free-form lesson prose. Text before the
    first ``## `` (a file header / comment) is ignored. A pitfall with no
    ``trigger:`` line parses but never matches — it's inert until the
    resident gives it a trigger. Deliberately not an error: a resident
    drafting a lesson before its triggers must be able to save the file.
    :func:`inert` is how that state gets *said* rather than merely
    tolerated (see #985).
    """
    path = dominion_dir / PITFALLS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    pitfalls: list[Pitfall] = []
    title: str | None = None
    triggers: list[str] = []
    body_lines: list[str] = []

    def _flush() -> None:
        if title is None:
            return
        pitfalls.append(
            Pitfall(title=title, triggers=triggers, body="\n".join(body_lines).strip())
        )

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            title = line[3:].strip()
            triggers = []
            body_lines = []
            continue
        if title is None:
            continue  # preamble before the first pitfall heading
        m = _TRIGGER_RE.match(line)
        if m:
            triggers.extend(
                t.strip() for t in m.group(1).split(",") if t.strip()
            )
            continue
        body_lines.append(line)
    _flush()
    return pitfalls


def match(pitfalls: list[Pitfall], task_text: str) -> list[Pitfall]:
    """Return pitfalls whose triggers fire for *task_text*, order preserved."""
    if not task_text:
        return []
    return [p for p in pitfalls if p.matches(task_text)]


def inert(pitfalls: list[Pitfall]) -> list[Pitfall]:
    """Return the entries that can never match — no triggers at all.

    The store's one silent failure mode. :func:`parse_pitfalls` accepts a
    triggerless entry on purpose (a body drafted before its triggers is
    legitimate), and :meth:`Pitfall.matches` then returns ``False`` for it
    against every task forever. On disk the entry is indistinguishable from
    a live one: same heading, same prose, same place in the file — so it
    reads as *filed* while behaving as *deleted*.

    Found live 2026-08-02 (#985): a lesson demoted out of the always-injected
    playbook into this trigger-gated store lost its ``trigger:`` line in the
    move and matched nothing for nine days. The demotion **is** the trigger
    line, which is why this state has to be reported at exactly the moment
    the memory system is otherwise working as designed.

    Order is the file's, so a report built from this reads top-to-bottom.
    """
    return [p for p in pitfalls if not any(t.strip() for t in p.triggers)]


def format_block(matched: list[Pitfall]) -> str:
    """Render matching pitfalls as a wake-prompt affordance block, or ``""``."""
    if not matched:
        return ""
    parts = [
        "# Pitfalls that match this task",
        "",
        "Failure-memory you recorded earlier, surfaced because a trigger in "
        "this task just hit it. Read it before you step on it again — and if "
        "you've since guarded the failure with a playbook invariant, lint, test, "
        "or baked tool, slash the pitfall (the forcing function is the better "
        "memory).",
    ]
    for p in matched:
        parts.append("")
        parts.append(f"## {p.title}")
        if p.body:
            parts.append(p.body)
    return "\n".join(parts)
