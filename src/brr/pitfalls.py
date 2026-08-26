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

# Failure-memory is the one injected block whose reader cannot know to pull it
# before the trigger fires.  It therefore keeps a real wake-time budget rather
# than becoming a pointer-only block, but no longer gets to grow without bound.
DEFAULT_INJECT_BUDGET_BYTES = 12 * 1024
_HANDLE_BODY_BYTES = 700


@dataclass
class Pitfall:
    """One recorded failure-memory: a titled lesson keyed by triggers."""

    title: str
    triggers: list[str] = field(default_factory=list)
    body: str = ""

    def matches(self, text: str) -> bool:
        """True if any trigger appears as a term or phrase in *text*.

        Word characters at a trigger's edges must also be word edges in the
        task.  Punctuation-led loci (``.brr/``, ``event:``) retain literal
        matching.  This keeps short triggers such as ``pr`` from firing inside
        ``prompt`` while preserving the path-shaped triggers the store uses.
        """
        return bool(self.matched_triggers(text))

    def matched_triggers(self, text: str) -> list[str]:
        """The triggers that fire, in their authored order."""
        return [trigger for trigger in self.triggers if _trigger_matches(trigger, text)]

    def match_score(self, text: str) -> tuple[int, int, int]:
        """Specific matches first; equal scores retain the file's order."""
        matched = self.matched_triggers(text)
        return (
            len(matched),
            max((len(trigger.split()) for trigger in matched), default=0),
            max((len(trigger) for trigger in matched), default=0),
        )


def _trigger_matches(trigger: str, text: str) -> bool:
    trigger = trigger.strip()
    if not trigger or not text:
        return False
    pattern = re.escape(trigger)
    if trigger[0].isalnum() or trigger[0] == "_":
        pattern = rf"(?<!\w){pattern}"
    if trigger[-1].isalnum() or trigger[-1] == "_":
        pattern = rf"{pattern}(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


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
    """Return matching pitfalls, strongest trigger evidence first.

    Python's sort is stable, so equal-strength matches keep the file order.
    """
    if not task_text:
        return []
    matched = [p for p in pitfalls if p.matches(task_text)]
    return sorted(matched, key=lambda p: p.match_score(task_text), reverse=True)


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


def _byte_head(text: str, limit: int) -> str:
    """A UTF-8-safe head of *text* no larger than *limit* bytes."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker = "…"
    room = max(0, limit - len(marker.encode("utf-8")))
    return encoded[:room].decode("utf-8", errors="ignore").rstrip() + marker


def _handle_body(body: str) -> str:
    """The opening paragraph: enough to identify the lesson, not replay it."""
    paragraphs = re.split(r"\n\s*\n", body.strip(), maxsplit=1)
    opening = paragraphs[0] if paragraphs and paragraphs[0] else ""
    return _byte_head(opening, _HANDLE_BODY_BYTES)


def format_block(
    matched: list[Pitfall], *, budget_bytes: int = DEFAULT_INJECT_BUDGET_BYTES
) -> str:
    """Render ranked failure-memory handles inside a hard byte budget."""
    if not matched:
        return ""
    parts = [
        "# Pitfalls that match this task",
        "",
        "Failure-memory you recorded earlier, surfaced because a trigger in "
        "this task just hit it. Each match is a handle — title, trigger "
        "vocabulary, opening rule — not the whole incident report. Full entries "
        "remain in the resident dominion's `pitfalls.md`; re-run `brnrd agent "
        "inject --task <topic>` after a topic shift. Slash an entry once a lint, "
        "test, hook, or baked tool guards it.",
    ]
    kept = 0
    for p in matched:
        entry = ["", f"## {p.title}"]
        triggers = ", ".join(p.triggers)
        if triggers:
            # Keep the store's own metadata spelling. A rendered block pasted
            # back into pitfalls.md must still parse as the same entry.
            entry.append(f"trigger: {triggers}")
        opening = _handle_body(p.body)
        if opening:
            entry.extend(("", opening))
        omitted = len(matched) - (kept + 1)
        footer = (
            f"\n\n_({omitted} lower-ranked matching pitfall"
            f"{'s' if omitted != 1 else ''} omitted by the "
            f"{budget_bytes:,} B block budget · full store: dominion "
            "`pitfalls.md`)_"
            if omitted else ""
        )
        candidate = "\n".join(parts + entry) + footer
        if len(candidate.encode("utf-8")) > max(0, budget_bytes):
            break
        parts.extend(entry)
        kept += 1
    omitted = len(matched) - kept
    if omitted:
        footer = (
            f"_({omitted} lower-ranked matching pitfall"
            f"{'s' if omitted != 1 else ''} omitted by the "
            f"{budget_bytes:,} B block budget · full store: dominion "
            "`pitfalls.md`)_"
        )
        # Default/configured practical budgets always fit this. A pathological
        # tiny override degrades to an empty block instead of violating its own
        # advertised hard ceiling.
        if kept == 0:
            parts = [parts[0]]
        candidate = "\n".join(parts + ["", footer])
        if len(candidate.encode("utf-8")) <= max(0, budget_bytes):
            parts.extend(("", footer))
    rendered = "\n".join(parts)
    return rendered if len(rendered.encode("utf-8")) <= max(0, budget_bytes) else ""
