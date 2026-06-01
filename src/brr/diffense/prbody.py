"""Project a diffense pack into a humanized Markdown PR body.

A **lossy fallback** (design → "PR body as a lossy projection"): the pack
is the design anchor; this Markdown is what a forge-only reviewer — the
phone reader who never opens the rich surface — gets for free in the PR
description. Everything here is mechanical: the *prose* is the cards' own
LLM-authored fields (the summary gloss, each uncertainty headline, the
walkthrough narrative), assembled under stable headings. Pure functions,
no I/O.

The full pack rides alongside the prose inside an HTML-comment marker
(``PACK_MARKER_BEGIN`` … ``PACK_MARKER_END``) when it fits the forge body
budget, so the PR is self-describing: a tool can recover the exact pack
from the body without a side channel. When it doesn't fit, the prose
stands alone and a pointer replaces the embed.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

# Uncertainty cards split into two reader-facing buckets by subkind: the
# doubts read as "⚠ Concerns", the scope notes as "Deferred / open" (what
# the change deliberately did *not* do). An unknown/absent subkind falls to
# Concerns — surface it rather than hide it.
_DEFERRED_SUBKINDS = {"out-of-scope-flag", "follow-up"}

# Cards that *describe the change surface* feed "Touched"; the orienting and
# doubt cards do not (they have their own sections).
_NON_TOUCHED_KINDS = {"summary", "uncertainty", "walkthrough"}

PACK_MARKER_BEGIN = "<!-- diffense:pack:v1"
PACK_MARKER_END = "diffense:pack:v1 -->"

# GitHub caps a PR body at 65536 chars. Keep headroom above the embedded
# pack for the prose; past this budget we drop the embed for a pointer.
_BODY_BUDGET = 60000


# ── Card access ──────────────────────────────────────────────────────


def _cards(pack: dict) -> list[dict]:
    cards = pack.get("cards")
    return [c for c in cards if isinstance(c, dict)] if isinstance(cards, list) else []


def _card_index(pack: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for card in _cards(pack):
        cid = card.get("id")
        if isinstance(cid, str) and cid:
            index.setdefault(cid, card)
    return index


def _summary_card(pack: dict) -> dict | None:
    for card in _cards(pack):
        if card.get("kind") == "summary":
            return card
    return None


def _gloss(card: dict) -> str:
    """The card's lead sentence — descriptive lore, or an uncertainty headline."""
    lore = card.get("lore")
    if isinstance(lore, dict) and isinstance(lore.get("descriptive"), str):
        if lore["descriptive"].strip():
            return lore["descriptive"].strip()
    headline = card.get("headline")
    return headline.strip() if isinstance(headline, str) else ""


def _label(card: dict) -> str:
    ident = card.get("identity")
    if isinstance(ident, dict) and isinstance(ident.get("label"), str):
        return ident["label"].strip()
    return ""


def _first_sentence(text: str, limit: int = 160) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    for sep in (". ", " — ", "; "):
        i = text.find(sep)
        if 0 < i < limit:
            return text[:i].strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _split_uncertainties(pack: dict) -> tuple[list[dict], list[dict]]:
    concerns: list[dict] = []
    deferred: list[dict] = []
    for card in _cards(pack):
        if card.get("kind") != "uncertainty":
            continue
        if card.get("subkind") in _DEFERRED_SUBKINDS:
            deferred.append(card)
        else:
            concerns.append(card)
    return concerns, deferred


# ── Title ────────────────────────────────────────────────────────────


def pr_title(pack: dict, *, fallback: str = "") -> str:
    """The PR title: explicit metadata title, else the summary card label."""
    meta = pack.get("metadata")
    if isinstance(meta, dict):
        pr = meta.get("pr")
        if isinstance(pr, dict) and isinstance(pr.get("title"), str) and pr["title"].strip():
            return pr["title"].strip()
    summary = _summary_card(pack)
    if summary:
        label = _label(summary)
        if label:
            return label
    return fallback


# ── Sections ─────────────────────────────────────────────────────────


def _severity_tally(cards: list[dict]) -> str:
    counts = Counter(
        str(c.get("severity")).strip()
        for c in cards
        if isinstance(c.get("severity"), str) and c.get("severity").strip()
    )
    parts = [f"{n} {sev}" for sev, n in counts.items() if sev]
    return f" ({', '.join(parts)})" if parts else ""


def _summary_section(pack: dict, concerns: list[dict]) -> str:
    card = _summary_card(pack)
    lines: list[str] = []
    if card:
        gloss = _gloss(card)
        if gloss:
            lines += [gloss, ""]
        shape = card.get("shape")
        if isinstance(shape, dict):
            arcs = shape.get("arcs")
            if isinstance(arcs, list) and arcs:
                lines.append("**Shape**")
                for arc in arcs:
                    if not isinstance(arc, dict):
                        continue
                    theme = str(arc.get("theme", "")).strip()
                    what = str(arc.get("what", "")).strip()
                    if theme and what:
                        lines.append(f"- _{theme}_ — {what}")
                    elif theme or what:
                        lines.append(f"- {theme or what}")
                lines.append("")
            surface = shape.get("surface_area")
            if isinstance(surface, list) and surface:
                items = ", ".join(f"`{s}`" for s in surface if s)
                if items:
                    lines += [f"**Surface** — {items}", ""]
    if concerns:
        lines.append(
            f"_{len(concerns)} concern(s){_severity_tally(concerns)} flagged below — "
            "read those first._"
        )
    body = "\n".join(lines).strip()
    if not body:
        return ""
    return "## Summary\n\n" + body


def _uncertainty_bullet(card: dict) -> str:
    subkind = str(card.get("subkind") or "note").strip()
    severity = str(card.get("severity") or "").strip()
    tag = f"{subkind} · {severity}" if severity else subkind
    head = card.get("headline") or _gloss(card) or _label(card) or "(no headline)"
    line = f"- **[{tag}]** {head.strip()}"
    resolution = card.get("proposed_resolution")
    if isinstance(resolution, str) and resolution.strip():
        line += f"\n  - _resolution:_ {resolution.strip()}"
    return line


def _concerns_section(concerns: list[dict]) -> str:
    if not concerns:
        return ""
    return "## ⚠ Concerns\n\n" + "\n".join(_uncertainty_bullet(c) for c in concerns)


def _narrative_section(pack: dict) -> str:
    for card in _cards(pack):
        if card.get("kind") == "walkthrough":
            gloss = _gloss(card)
            if gloss:
                return "## Narrative\n\n" + gloss
    return ""


def _touched_section(pack: dict) -> str:
    rows: list[str] = []
    seen: set[tuple[str, str]] = set()
    for card in _cards(pack):
        if card.get("kind") in _NON_TOUCHED_KINDS:
            continue
        ident = card.get("identity")
        if not isinstance(ident, dict):
            continue
        path = ident.get("file")
        if not isinstance(path, str) or not path:
            continue
        what = _first_sentence(_gloss(card)) or _label(card)
        key = (path, what)
        if key in seen:
            continue
        seen.add(key)
        rows.append(f"- `{path}` — {what}" if what else f"- `{path}`")
    if not rows:
        return ""
    return "## Touched\n\n" + "\n".join(rows)


def _reading_order_section(pack: dict) -> str:
    order = pack.get("reading_order")
    if not isinstance(order, list) or not order:
        return ""
    index = _card_index(pack)
    rows: list[str] = []
    for i, cid in enumerate(order, 1):
        card = index.get(cid) if isinstance(cid, str) else None
        label = _label(card) if card else ""
        rows.append(f"{i}. {label or cid}")
    return "## Reading order\n\n" + "\n".join(rows)


def _deferred_section(deferred: list[dict]) -> str:
    if not deferred:
        return ""
    return "## Deferred / open\n\n" + "\n".join(_uncertainty_bullet(c) for c in deferred)


def _footer(pack: dict, body_so_far: str, embed_pack: bool) -> str:
    note = "_Generated by brr · diffense review pack. `brr review` renders the full graph._"
    if not embed_pack:
        return note
    blob = json.dumps(pack, separators=(",", ":"), ensure_ascii=False)
    if len(body_so_far) + len(blob) + len(note) + 64 > _BODY_BUDGET:
        return (
            note
            + "\n\n_(Full pack omitted from this body — too large to embed; "
            "render it locally with `brr review`.)_"
        )
    return f"{note}\n\n{PACK_MARKER_BEGIN}\n{blob}\n{PACK_MARKER_END}"


def project_pr_body(pack: dict, *, embed_pack: bool = True) -> str:
    """Render *pack* as a Markdown PR body (the lossy, forge-readable surface).

    Sections appear only when the pack has material for them: Summary,
    ⚠ Concerns, Narrative (a walkthrough card), Touched, Reading order,
    Deferred / open. The full pack is embedded in a trailing HTML-comment
    marker when it fits ``_BODY_BUDGET``.
    """
    concerns, deferred = _split_uncertainties(pack)
    sections = [
        _summary_section(pack, concerns),
        _concerns_section(concerns),
        _narrative_section(pack),
        _touched_section(pack),
        _reading_order_section(pack),
        _deferred_section(deferred),
    ]
    body = "\n\n".join(s for s in sections if s.strip())
    footer = _footer(pack, body, embed_pack)
    return (f"{body}\n\n{footer}" if body else footer).strip() + "\n"


def extract_pack(body: str) -> dict[str, Any] | None:
    """Recover an embedded pack from a PR body, or ``None`` if absent/bad.

    The inverse of the ``project_pr_body`` embed: lets a reader (``brr
    review``, a renderer) reconstruct the exact pack from the forge body
    without a side channel.
    """
    if not body or PACK_MARKER_BEGIN not in body:
        return None
    after = body.split(PACK_MARKER_BEGIN, 1)[1]
    if PACK_MARKER_END not in after:
        return None
    blob = after.split(PACK_MARKER_END, 1)[0].strip()
    try:
        pack = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return pack if isinstance(pack, dict) else None
