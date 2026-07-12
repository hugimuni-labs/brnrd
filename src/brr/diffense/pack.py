"""The diffense review-pack contract: schema + validator.

A *pack* is the JSON artifact a runner emits to describe a change for
review (see ``kb/design-diffense.md``). This module is the locked
contract every producer and renderer agrees on, and the engine behind
``brnrd review --check``: it loads a pack, validates its structure and
card graph, resolves every code/kb locator against the repo, and runs
the cheap end of the six-clamp discipline as lints.

The taxonomy is an **open core**, not a closed enum (design → "The kind
set is an open core"): a small set of well-known kinds is special-cased,
and anything else is a ``custom`` card that still must carry the
always-present axes. So the validator is *strict on the load-bearing
spine* — id uniqueness, the always-present axes, a resolvable locator on
any card that names a file, and a card graph with no dangling card-to-
card edges — and *permissive on kind*: an unknown kind is a warning, not
an error, so the format can grow from real use the way the kb does.

Locator resolution is the headline ``--check`` value: it is what would
have caught the design draft's invented ``cache.get_with_etag`` symbol
(see ``kb/diffense-prototype-pr64.md``). A pack with a dead reference
must not publish.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# ── Taxonomy (open core) ─────────────────────────────────────────────

# Kinds renderers and --check special-case. Anything else is allowed as
# a `custom` card (warned, never errored) so the format grows from use.
KNOWN_KINDS = frozenset(
    {
        "summary",
        "uncertainty",
        "walkthrough",
        "code-fn-edit",
        "code-fn-new",
        "code-fn-delete",
        "code-module-split",
        "code-restructure",
        "code-move",
        "kb-page-edit",
        "kb-page-new",
        "kb-page-split",
        "lifecycle-flip",
        "test-add",
        "dep-add",
        "custom",
    }
)

UNCERTAINTY_SUBKINDS = frozenset(
    {"assumption", "concern", "dilemma", "out-of-scope-flag", "follow-up", "meta"}
)

SEVERITIES = frozenset({"low", "med", "high", "blocking", "blocking-for-merge"})

# Card ids namespace by prefix (``item:foo``, ``unc:bar`` …). An edge or
# reading-order entry using one of these prefixes is a *card* reference
# and must resolve; any other string is a free reference (a symbol, a kb
# anchor) and is left alone.
_CARD_ID_PREFIXES = ("item:", "unc:", "walk:", "summary:")

# Cheap clamp budgets. Generous on purpose — a lint flags the obviously
# unsharp, it does not adjudicate taste.
_GLOSS_CHAR_BUDGET = 600
_PRESCRIPTIVE_SMELLS = (
    "you should",
    "you must",
    "must use",
    "best practice",
    "the cornerstone",
    "we recommend",
    "i recommend",
    "is recommended",
)


# ── Issues ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Issue:
    """One validation finding. ``error`` blocks publish; ``warning`` does not."""

    level: str  # "error" | "warning"
    code: str  # stable machine code, e.g. "locator.unresolved"
    message: str
    card_id: str | None = None

    def format(self) -> str:
        where = f" [{self.card_id}]" if self.card_id else ""
        return f"{self.level.upper():7} {self.code}{where}: {self.message}"


def _err(code: str, message: str, card_id: str | None = None) -> Issue:
    return Issue("error", code, message, card_id)


def _warn(code: str, message: str, card_id: str | None = None) -> Issue:
    return Issue("warning", code, message, card_id)


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.level == "error" for i in issues)


# ── Loading ──────────────────────────────────────────────────────────


class PackError(Exception):
    """The pack file is missing or not valid JSON — checking can't begin."""


def load_pack(path: Path) -> dict:
    """Read and JSON-parse a pack file. Raises ``PackError`` on either failure."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise PackError(f"cannot read pack: {e}") from e
    try:
        pack = json.loads(text)
    except json.JSONDecodeError as e:
        raise PackError(f"pack is not valid JSON: {e}") from e
    if not isinstance(pack, dict):
        raise PackError("pack must be a JSON object")
    return pack


# ── Checking ─────────────────────────────────────────────────────────


def check_pack(pack: dict, *, repo_root: Path | None = None) -> list[Issue]:
    """Full ``--check``: structure + card graph + locators + clamp lints.

    *repo_root* anchors locator resolution against the working tree; when
    omitted the locator checks are skipped (structure-only mode).
    """
    issues = validate_structure(pack)
    # The graph and clamp passes index cards by id; only run them once the
    # spine is sane enough that ``cards`` is a list of id-bearing objects.
    cards = pack.get("cards")
    if isinstance(cards, list):
        issues += validate_graph(pack)
        issues += clamp_lints(pack)
        if repo_root is not None:
            issues += resolve_locators(pack, repo_root)
    return issues


def _card_index(pack: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for card in pack.get("cards", []):
        if isinstance(card, dict) and isinstance(card.get("id"), str):
            index.setdefault(card["id"], card)
    return index


def _gloss(card: dict) -> str:
    """The card's lead sentence — descriptive lore, or an uncertainty headline."""
    lore = card.get("lore")
    if isinstance(lore, dict) and isinstance(lore.get("descriptive"), str):
        if lore["descriptive"].strip():
            return lore["descriptive"]
    headline = card.get("headline")
    return headline if isinstance(headline, str) else ""


def validate_structure(pack: dict) -> list[Issue]:
    """Top-level shape + per-card always-present axes."""
    issues: list[Issue] = []

    if not isinstance(pack.get("schema_version"), str):
        issues.append(_err("pack.schema-version", "missing string `schema_version`"))
    if not isinstance(pack.get("metadata"), dict):
        issues.append(_err("pack.metadata", "missing object `metadata`"))

    cards = pack.get("cards")
    if not isinstance(cards, list) or not cards:
        issues.append(_err("pack.cards", "`cards` must be a non-empty array"))
        return issues

    seen: set[str] = set()
    summary_count = 0
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            issues.append(_err("card.shape", f"cards[{i}] is not an object"))
            continue
        cid = card.get("id")
        if not isinstance(cid, str) or not cid:
            issues.append(_err("card.id", f"cards[{i}] missing string `id`"))
            cid = None
        elif cid in seen:
            issues.append(_err("card.id-duplicate", f"duplicate card id", cid))
        else:
            seen.add(cid)

        kind = card.get("kind")
        if not isinstance(kind, str) or not kind:
            issues.append(_err("card.kind", "missing string `kind`", cid))
        else:
            if kind == "summary":
                summary_count += 1
            if kind not in KNOWN_KINDS:
                issues.append(
                    _warn(
                        "card.kind-unknown",
                        f"unknown kind `{kind}` (renders as generic; "
                        "raise a meta uncertainty card if it should be promoted)",
                        cid,
                    )
                )

        issues += _validate_always_axes(card, cid)
        if kind == "uncertainty":
            issues += _validate_uncertainty(card, cid)

    if summary_count == 0:
        issues.append(_warn("pack.no-summary", "no summary card (the orienting header)"))
    elif summary_count > 1:
        issues.append(
            _err("pack.multi-summary", f"{summary_count} summary cards; expected one")
        )

    return issues


def _validate_always_axes(card: dict, cid: str | None) -> list[Issue]:
    """Identity, gloss, provenance, and a locator when the card names a file."""
    issues: list[Issue] = []

    identity = card.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("label"), str):
        issues.append(_err("card.identity", "missing `identity.label`", cid))
        identity = identity if isinstance(identity, dict) else {}

    if not _gloss(card).strip():
        issues.append(
            _err("card.gloss", "missing gloss (`lore.descriptive` or `headline`)", cid)
        )

    prov = card.get("provenance")
    if not isinstance(prov, dict):
        issues.append(_warn("card.provenance", "missing `provenance`", cid))

    # Any card that names a file must carry a resolvable local locator —
    # "no dead references" (design → always-present axes).
    names_file = isinstance(identity.get("file"), str) and identity.get("file")
    if names_file:
        locator = card.get("locator")
        if not isinstance(locator, dict) or not isinstance(locator.get("local"), str):
            issues.append(
                _err("card.locator", "names a file but has no `locator.local`", cid)
            )
    return issues


def _validate_uncertainty(card: dict, cid: str | None) -> list[Issue]:
    issues: list[Issue] = []
    subkind = card.get("subkind")
    if not isinstance(subkind, str):
        issues.append(_err("uncertainty.subkind", "missing `subkind`", cid))
    elif subkind not in UNCERTAINTY_SUBKINDS:
        issues.append(
            _warn("uncertainty.subkind-unknown", f"unknown subkind `{subkind}`", cid)
        )
    severity = card.get("severity")
    if not isinstance(severity, str):
        issues.append(_err("uncertainty.severity", "missing `severity`", cid))
    elif severity not in SEVERITIES:
        issues.append(
            _warn("uncertainty.severity-unknown", f"unusual severity `{severity}`", cid)
        )
    return issues


def _is_card_ref(target: str) -> bool:
    return target.startswith(_CARD_ID_PREFIXES)


def validate_graph(pack: dict) -> list[Issue]:
    """reading_order coverage + every card-namespaced reference resolves."""
    issues: list[Issue] = []
    index = _card_index(pack)
    ids = set(index)

    def check_ref(target: Any, code: str, cid: str | None, what: str) -> None:
        # Only references using a card-id namespace are expected to be
        # cards; a bare symbol or kb anchor is a free reference.
        if isinstance(target, str) and _is_card_ref(target) and target not in ids:
            issues.append(_err(code, f"{what} -> unknown card `{target}`", cid))

    order = pack.get("reading_order")
    if order is not None and not isinstance(order, list):
        issues.append(_err("pack.reading-order", "`reading_order` must be an array"))
        order = None
    if isinstance(order, list):
        for entry in order:
            if entry not in ids:
                issues.append(
                    _err("reading-order.unknown", f"reading_order -> unknown card `{entry}`")
                )
        if order and order[0] in index and index[order[0]].get("kind") != "summary":
            issues.append(
                _warn("reading-order.summary-first", "summary card should read first")
            )
        listed = set(order)
        for cid in ids:
            if cid not in listed:
                issues.append(
                    _warn("reading-order.missing", "card absent from reading_order", cid)
                )

    for cid, card in index.items():
        for edge in _as_list(card.get("lateral_edges")):
            if not isinstance(edge, dict):
                continue
            target = edge.get("target") or edge.get("card")
            if target is None and "locator" not in edge and "ref" not in edge:
                issues.append(_warn("edge.empty", "lateral edge has no target", cid))
                continue
            check_ref(target, "edge.unknown", cid, f"edge `{edge.get('type', '?')}`")
        for member in _as_list(card.get("members")):
            if isinstance(member, dict):
                check_ref(member.get("card"), "member.unknown", cid, "walkthrough member")
        for stage in _as_list(card.get("stages")):
            if isinstance(stage, dict) and "card" in stage:
                check_ref(stage.get("card"), "stage.unknown", cid, "data-trace stage")

    return issues


def resolve_locators(pack: dict, repo_root: Path) -> list[Issue]:
    """Resolve each ``locator.local`` against the working tree.

    A missing file or an out-of-range line is an error (a dead reference);
    a named ``identity.symbol`` absent from the file is a heuristic
    warning (it catches invented symbols without false-failing on dotted
    or renamed names).
    """
    issues: list[Issue] = []
    for card in pack.get("cards", []):
        if not isinstance(card, dict):
            continue
        cid = card.get("id") if isinstance(card.get("id"), str) else None
        locator = card.get("locator")
        if not isinstance(locator, dict):
            continue
        local = locator.get("local")
        if not isinstance(local, str) or not local:
            continue

        rel, line = _split_local(local)
        path = (repo_root / rel).resolve()
        # Containment guard: a locator must point inside the repo.
        try:
            path.relative_to(repo_root.resolve())
        except ValueError:
            issues.append(_err("locator.escapes-repo", f"`{rel}` escapes the repo", cid))
            continue
        if not path.exists():
            issues.append(_err("locator.unresolved", f"`{rel}` does not exist", cid))
            continue
        if line is not None and path.is_file():
            text = _read_text(path)
            if text is not None:
                n_lines = text.count("\n") + 1
                if line > n_lines:
                    issues.append(
                        _err(
                            "locator.line-out-of-range",
                            f"`{rel}` has {n_lines} lines; locator points at L{line}",
                            cid,
                        )
                    )
                else:
                    issues += _check_symbol(card, text, rel, cid)
    return issues


def _check_symbol(card: dict, text: str, rel: str, cid: str | None) -> list[Issue]:
    identity = card.get("identity")
    if not isinstance(identity, dict):
        return []
    symbol = identity.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        return []
    # Match the final dotted component as a whole word: catches an
    # invented `cache.get_with_etag` while tolerating `module.func`
    # qualification and prose-y symbols ("whole page", "package split").
    leaf = symbol.split(".")[-1].strip()
    if not leaf or " " in leaf or not _looks_like_identifier(leaf):
        return []
    if leaf not in text:
        return [
            _warn(
                "locator.symbol-not-found",
                f"`{symbol}` not found in {rel} (renamed, or invented?)",
                cid,
            )
        ]
    return []


def clamp_lints(pack: dict) -> list[Issue]:
    """The cheap, mechanical end of the six clamps (warnings only)."""
    issues: list[Issue] = []
    for card in pack.get("cards", []):
        if not isinstance(card, dict):
            continue
        cid = card.get("id") if isinstance(card.get("id"), str) else None

        gloss = _gloss(card)
        if len(gloss) > _GLOSS_CHAR_BUDGET:
            issues.append(
                _warn(
                    "clamp.sharp",
                    f"gloss is {len(gloss)} chars (budget {_GLOSS_CHAR_BUDGET}); "
                    "push depth into zoom levels",
                    cid,
                )
            )

        # emit-iff-honest: a conditional axis present but empty is noise.
        for axis in ("lateral_edges", "entry_stats", "stages", "members", "exercising_tests"):
            if axis in card and isinstance(card[axis], list) and not card[axis]:
                issues.append(
                    _warn("clamp.emit-iff-honest", f"empty `{axis}` was emitted", cid)
                )
        lore = card.get("lore")
        if isinstance(lore, dict) and "possibility" in lore and not str(lore["possibility"]).strip():
            issues.append(
                _warn("clamp.emit-iff-honest", "empty `lore.possibility` was emitted", cid)
            )

        haystack = " ".join(
            s for s in (gloss, _possibility(card)) if s
        ).lower()
        for smell in _PRESCRIPTIVE_SMELLS:
            if smell in haystack:
                issues.append(
                    _warn(
                        "clamp.non-prescriptive",
                        f"prescriptive phrasing: `{smell}` (cards describe; "
                        "the reviewer decides)",
                        cid,
                    )
                )
                break
    return issues


# ── small helpers ────────────────────────────────────────────────────


def _possibility(card: dict) -> str:
    lore = card.get("lore")
    if isinstance(lore, dict) and isinstance(lore.get("possibility"), str):
        return lore["possibility"]
    return ""


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _split_local(local: str) -> tuple[str, int | None]:
    """Split a ``path`` or ``path:line`` (or ``path:start-end``) locator."""
    rel, sep, tail = local.partition(":")
    if not sep:
        return local, None
    head = tail.split("-", 1)[0].strip()
    try:
        return rel, int(head)
    except ValueError:
        return rel, None


def _looks_like_identifier(token: str) -> bool:
    return all(ch.isalnum() or ch == "_" for ch in token)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
