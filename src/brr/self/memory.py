"""Home operations: deterministic wake, redacted encodings, and checkpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from .. import dominion, knowledge, pitfalls
from ..hooks import _tool_detail, _tool_why, redact_detail
from .home import require_home, write_text


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def home_sources(home: Path) -> list[knowledge.KnowledgeSource]:
    return [knowledge.KnowledgeSource("self knowledge", home / "kb", "self")]


def wake(home: Path, situation: str = "", *, budget_bytes: int = 16384) -> str:
    """Write a wake; identity and omission accounting sit outside the slice cap.

    The existing selector's collapse markers may exceed its content budget;
    retain them intact rather than hiding the fact that something was lost.
    ``self-inject`` is resident-authored, so selector semantics stay unchanged.
    """
    require_home(home)
    if budget_bytes < 0:
        raise ValueError("budget must be nonnegative")
    situation = redact_detail(situation.strip())
    # Identity is the always-on floor. Remaining content shares a budget.
    digest = dominion.resolve_self_inject(home, budget_bytes=budget_bytes * 2 // 3)
    matched = pitfalls.match(pitfalls.parse_pitfalls(home), situation)
    pit_block = pitfalls.format_block(matched, budget_bytes=budget_bytes // 6)
    # The shared renderer's daemon directions aren't useful on a foreign body.
    pit_block = pit_block.replace("dominion `pitfalls.md`", "home `pitfalls.md`").replace(
        '`brnrd agent inject --task <topic>`', '`self wake --situation <topic>`')
    hits = knowledge.search(home, situation, search_sources=home_sources(home), limit=10)
    recall_lines = []
    used = 0
    selected = set()
    for hit in hits:
        line = f"- {hit.path.relative_to(home)}:{hit.line_no}: {hit.line}"
        size = len(line.encode("utf-8")) + 1
        if used + size <= budget_bytes // 6:
            recall_lines.append(line)
            used += size
            selected.add(hit.path.resolve())
    omitted = []
    for hit in hits:
        if hit.path.resolve() not in selected:
            omitted.append(f"{hit.path.relative_to(home)} (recall budget)")
    for path in sorted((home / "kb").rglob("*.md")):
        if path.resolve() not in selected:
            name = str(path.relative_to(home))
            if not any(entry.startswith(name + " ") for entry in omitted):
                omitted.append(f"{name} (outside situational slice; use recall)")
    kept_titles = {line[3:] for line in pit_block.splitlines() if line.startswith("## ")}
    for pitfall in matched:
        if pitfall.title not in kept_titles:
            omitted.append(f"pitfalls.md: {pitfall.title} (pitfall budget)")
    for pitfall in pitfalls.parse_pitfalls(home):
        if not pitfall.matches(situation):
            omitted.append(f"pitfalls.md: {pitfall.title} (trigger did not match)")
    # The selector reports budget losses inline; name missing or unsupported
    # manifest entries too, which the shared resolver deliberately skips.
    for line in _read(home / "self-inject").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        mode, _, target = line.strip().partition(" ")
        if not dominion._render_entry(home, mode, target.strip()):
            omitted.append(f"{line.strip()} (missing, empty, unsafe or unsupported entry)")
    parts = ["# Self wake", f"Home: {home}", "## Identity", _read(home / "identity.md")]
    if situation:
        parts += ["## Situation", situation]
    parts += ["## Memory slice", digest or "No self-inject entries resolved."]
    if pit_block:
        parts.append(pit_block)
    if recall_lines:
        parts += ["## Situational knowledge", "\n".join(recall_lines)]
    parts += ["## Outside this wake", "\n".join(f"- {v}" for v in dict.fromkeys(omitted))
              or "No additional situational pages omitted.",
              "Budget applies to memory content; identity and omission receipts are additional.\n"
              "Use recall for the long tail. Keep now.md current before stopping or compacting."]
    text = "\n\n".join(parts) + "\n"
    write_text(home / "wake.md", text)
    return text


def encode(home: Path, payload: dict) -> dict:
    """Keep a boundary's act and reason; never retain raw payload or output."""
    require_home(home)
    name, inputs = payload.get("tool_name"), payload.get("tool_input")
    if not isinstance(name, str) or not name.strip() or not isinstance(inputs, dict):
        raise ValueError("encode expects tool_name and an object tool_input")
    row = {"at": now(), "act": {"tool": redact_detail(name),
                               "detail": _tool_detail(name, inputs)},
           "why": _tool_why(name, inputs)}
    # Metadata is for replay association, not another route for raw text.
    for key in ("session_id", "tool_use_id"):
        if isinstance(payload.get(key), str):
            row[key] = redact_detail(payload[key][:200])
    path = home / "journal" / f'{row["at"][:10]}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    # One append syscall per bounded row; parallel hook calls don't interleave.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("Incomplete journal append")
    finally:
        os.close(fd)
    return row


def obligations(home: Path) -> list[dict]:
    require_home(home)
    return [{"path": str(p.relative_to(home)), "text": _read(p)}
            for p in sorted((home / "obligations").rglob("*.md"))
            if p.name != "README.md" and p.resolve().is_relative_to(home.resolve())]


def checkpoint(home: Path, payload: dict | None = None) -> Path:
    """Snapshot authored orientation and advancement, without inferred progress."""
    require_home(home)
    payload = payload or {}
    at = now()
    row = {"at": at, "event": payload.get("hook_event_name", "manual"),
           "session_id": redact_detail(str(payload.get("session_id", ""))[:200]),
           "orientation": redact_detail(_read(home / "now.md")),
           "obligations": [{**o, "text": redact_detail(o["text"])} for o in obligations(home)]}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = home / "journal" / "checkpoints" / f"{stamp}-{uuid.uuid4().hex[:8]}.json"
    write_text(path, json.dumps(row, indent=2, ensure_ascii=False) + "\n")
    return path


def note(home: Path, kind: str, text: str) -> Path:
    """Append an authored memory, not a consolidation or a curation decision."""
    require_home(home)
    paths = {"notebook": "notebook.md", "playbook": "playbook.md", "pitfall": "pitfalls.md",
             "knowledge": "kb/notes.md", "obligation": "obligations/notes.md", "now": "now.md"}
    if kind not in paths:
        raise ValueError(f"Unknown note kind; choose {', '.join(paths)}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("note text must be a nonempty string")
    path = home / paths[kind]
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n{text.strip()}\n")
    return path
