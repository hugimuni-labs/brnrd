"""The stake — one instrument for money, facing both ways (design-the-loom §16).

A request may carry a **stake**: how much of the window this ask may spend,
and a **cut-at**: the hard stop. Toward a strand the same instrument already
exists as ``allowance:``; toward the seat it is new. This module is the pure
half — grammar, normalisation, the meter, the chip — with no filesystem and
no daemon state. ``daemon.py`` owns persistence (``Run.meta["stake"]``) and
every effect (arming the hold, answering a message at the cut).

**Grammar.** ``stake: 5%`` (a share of the binding weekly window) or
``stake: 2m`` (weighted tokens — :func:`brr.allowance.parse_tokens`'s unit).
``cut-at:`` takes the same two forms; unset it is the stake × 1.5. A
``cut-at`` under its stake is refused: a hard stop that fires before the
stake is spent is not a stake.

**The unit.** Tokens are what the meter reads; the share is what a person
reads. Both are kept on the row. Normalising a share to tokens needs the
window's size in weighted tokens, and nothing in the tree measures that
(``allowance.py`` names the per-provider exchange rate as slice 3's, unbuilt,
and refuses to guess one). So the size is **config-owned** —
``stake.window_tokens`` — the same posture ``resident.allowance_tokens``
takes; unset, :data:`DEFAULT_WINDOW_TOKENS` applies and the row says
``window_basis: default`` so no reader mistakes it for a measurement.

Worked example (default window, 100m): ``stake: 5%`` → 5m tokens, cut-at
unset → 7.5m (7.5%). At a boundary where the stake has metered 1.1m, the chip
reads ``stake 1.1m/5% · 22%``; at 7.5m the frame arms ``stake_cut``.

**The meter.** The seat's own window-scoped spend
(:func:`brr.allowance.resident_allowance_state`, the same reading
``resources.allowance`` publishes) — never a second accounting. The stake
accumulates that reading's *positive deltas* from the moment it is armed, so a
window roll (the reading drops back toward zero) neither refunds nor double
charges. Spend before the run's first metered boundary is not charged — the
seat's own meter starts there too.
"""

from __future__ import annotations

import re
import time
from typing import Any, Mapping

from . import allowance

#: ``stake.window_tokens`` default — the binding weekly window's size in
#: weighted tokens when the operator has not sized it. **Not a measurement**:
#: a round number so ``1%`` reads as ``1m``, recorded as
#: ``window_basis: default`` on every row that used it.
DEFAULT_WINDOW_TOKENS = 100_000_000
WINDOW_TOKENS_KEY = "stake.window_tokens"

#: ``cut-at:`` unset ⇒ the stake × this.
DEFAULT_CUT_AT_FACTOR = 1.5

#: The seat's answer to a stake (outbox ``stake: refuse``).
REFUSE = "refuse"

_SHARE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*%\s*$")


class StakeError(ValueError):
    """A stake the grammar cannot read — its text is the notice."""


def parse_amount(raw: Any) -> dict[str, Any] | None:
    """``{"share_pct": float}`` or ``{"tokens": int}``; ``None`` when unset.

    Raises :class:`StakeError` on a value present but not in the grammar —
    never a silent default, the posture ``allowance:`` takes on a spawn.
    """
    text = str(raw if raw is not None else "").strip().strip("`")
    if not text:
        return None
    match = _SHARE_RE.match(text)
    if match:
        share = float(match.group(1))
        if share <= 0 or share > 100:
            raise StakeError(f"{text!r} is not a share of the window (0% < share ≤ 100%)")
        return {"share_pct": share}
    tokens = allowance.parse_tokens(text)
    if tokens is None:
        raise StakeError(
            f"{text!r} is not a stake — a share of the window (`5%`) or "
            "weighted tokens (`2m`, `500k`)"
        )
    return {"tokens": tokens}


def window_tokens(cfg: Mapping[str, Any] | None) -> tuple[int, str]:
    """``(tokens, basis)`` — ``basis`` is ``config`` or ``default``."""
    raw = (cfg or {}).get(WINDOW_TOKENS_KEY)
    tokens = allowance.parse_tokens(raw) if raw is not None else None
    if tokens is not None:
        return tokens, "config"
    return DEFAULT_WINDOW_TOKENS, "default"


def _both(amount: dict[str, Any], window: int) -> tuple[int, float]:
    if "share_pct" in amount:
        share = float(amount["share_pct"])
        return int(round(window * share / 100.0)), share
    tokens = int(amount["tokens"])
    return tokens, round(100.0 * tokens / window, 3)


def normalise(
    stake_raw: Any,
    cut_at_raw: Any = None,
    *,
    cfg: Mapping[str, Any] | None,
    now: float | None = None,
) -> dict[str, Any]:
    """One armed-stake row from the two raw values. Raises :class:`StakeError`."""
    stake = parse_amount(stake_raw)
    if stake is None:
        raise StakeError("no stake given")
    window, basis = window_tokens(cfg)
    stake_tokens, stake_share = _both(stake, window)
    cut = parse_amount(cut_at_raw)
    if cut is None:
        cut_tokens = int(round(stake_tokens * DEFAULT_CUT_AT_FACTOR))
        cut_share = round(stake_share * DEFAULT_CUT_AT_FACTOR, 3)
        cut_basis = "default"
    else:
        cut_tokens, cut_share = _both(cut, window)
        cut_basis = "given"
    if cut_tokens < stake_tokens:
        raise StakeError(
            f"cut-at {format_amount(cut, cut_tokens)} is under the stake "
            f"{format_amount(stake, stake_tokens)} — the hard stop must be at or above it"
        )
    return {
        "tokens": stake_tokens,
        "share_pct": stake_share,
        "unit": "share" if "share_pct" in stake else "tokens",
        "cut_at_tokens": cut_tokens,
        "cut_at_share_pct": cut_share,
        "cut_at_basis": cut_basis,
        "window_tokens": window,
        "window_basis": basis,
        "armed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }


def format_amount(amount: dict[str, Any] | None, tokens: int) -> str:
    if amount and "share_pct" in amount:
        return f"{float(amount['share_pct']):g}%"
    return allowance.format_tokens(tokens)


# ── where a request carries it ────────────────────────────────────────


def _lead_lines(body: str) -> dict[str, str]:
    """``stake:`` / ``cut-at:`` lines at the very top of a message body.

    A chat message has no frontmatter, so the one place a person can type a
    stake is the message itself. Only leading lines count, and only these
    two keys — a sentence that mentions a stake later is prose.
    """
    found: dict[str, str] = {}
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if found:
                break
            continue
        key, sep, value = stripped.partition(":")
        key = key.strip().strip("`").lower().replace("_", "-")
        if not sep or key not in ("stake", "cut-at") or key in found:
            break
        found[key] = value.strip().strip("`")
    return found


def request_from(frontmatter: Mapping[str, Any] | None, body: str = "") -> dict[str, str] | None:
    """The raw ``{"stake", "cut_at"}`` a request carries, or ``None``.

    Frontmatter first (``stake:`` + ``cut_at:``/``cut-at:``), else the
    body's lead lines. A ``cut-at`` alone is not a request, and an event the
    seat already refused (``stake_refused``) carries none.
    """
    fm = frontmatter or {}
    if str(fm.get("stake_refused") or "").strip():
        # The seat refused this stake (`stake: refuse`): the ask is parked
        # with its coordinates, never re-armed by a later read of the event.
        return None
    stake_raw = str(fm.get("stake") or "").strip()
    cut_raw = str(fm.get("cut_at") or fm.get("cut-at") or "").strip()
    if not stake_raw:
        lead = _lead_lines(body)
        stake_raw = lead.get("stake", "")
        cut_raw = cut_raw or lead.get("cut-at", "")
    if not stake_raw:
        return None
    return {"stake": stake_raw, "cut_at": cut_raw}


# ── the meter ─────────────────────────────────────────────────────────


def meter(row: dict[str, Any], reading: int | None) -> dict[str, Any]:
    """Fold one boundary's window-scoped spend *reading* into *row* (in place).

    Accumulates positive deltas since the last reading; a reading below the
    last (a window roll reset the seat's meter) counts from zero. ``None``
    leaves the row as it is — no reading is not a zero.
    """
    if reading is None:
        return row
    reading = int(reading)
    last = row.get("last_reading")
    spent = int(row.get("spent") or 0)
    if last is None:
        # First reading after arming: the seat's own meter already starts at
        # the run's first metered boundary, so the whole reading is this run's.
        # A stake armed mid-run pins `last_reading` at arm time instead.
        spent += max(0, reading)
    elif reading >= int(last):
        spent += reading - int(last)
    else:
        spent += max(0, reading)
    row["last_reading"] = reading
    row["spent"] = spent
    window = int(row.get("window_tokens") or 0)
    row["spent_share_pct"] = round(100.0 * spent / window, 3) if window else None
    row["pct"] = allowance.spend_pct(spent, row.get("tokens"))
    return row


def at_cut(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict) or row.get("state") != "armed":
        return False
    try:
        return int(row.get("spent") or 0) >= int(row["cut_at_tokens"])
    except (KeyError, TypeError, ValueError):
        return False


def chip(row: dict[str, Any] | None) -> str | None:
    """``stake 1.1m/5% · 22%`` — the stake in the unit it was given."""
    if not isinstance(row, dict) or row.get("state") not in ("armed", "cut"):
        return None
    spent = int(row.get("spent") or 0)
    if row.get("unit") == "share":
        denominator = f"{float(row.get('share_pct') or 0):g}%"
    else:
        denominator = allowance.format_tokens(row.get("tokens"))
    pct = allowance.spend_pct(spent, row.get("tokens"))
    text = f"stake {allowance.format_tokens(spent)}/{denominator}"
    if pct is not None:
        text += f" · {pct:.0f}%"
    return text


def boundary_projection(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """What rides the boundary row's ``spend.stake`` and the portal facet."""
    if not isinstance(row, dict) or not row.get("state"):
        return None
    keys = (
        "state", "tokens", "share_pct", "unit", "cut_at_tokens", "cut_at_share_pct",
        "spent", "spent_share_pct", "pct", "window_tokens", "window_basis",
        "event_id", "refused",
    )
    return {key: row.get(key) for key in keys}


def directive_line(row: dict[str, Any]) -> str:
    return (
        f"- stake cut-at reached ({allowance.format_tokens(row.get('spent'))} of "
        f"{allowance.format_tokens(row.get('cut_at_tokens'))}) — the seat parks "
        "when this turn ends (`parked·stake_cut`); close what you can say now. "
        "Only the user's `stake:` on the thread raises it."
    )


def cut_terms(row: dict[str, Any] | None) -> str:
    """The one paragraph a seat parked at its cut-at owes a correspondent."""
    row = row if isinstance(row, dict) else {}
    spent = allowance.format_tokens(row.get("spent"))
    cut = allowance.format_tokens(row.get("cut_at_tokens"))
    share = row.get("cut_at_share_pct")
    share_text = f" ({float(share):g}% of the window)" if isinstance(share, (int, float)) else ""
    return (
        f"Parked at the stake's cut-at — {spent} spent of {cut}{share_text}. "
        "Messages sent now are kept, not answered. Send `stake: <share or tokens>` "
        "(for example `stake: 8%`) to raise it and resume; a message without a "
        "stake does not."
    )
