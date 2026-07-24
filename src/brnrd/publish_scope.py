"""Server-side publish-scope consent — repo connect, legal pack item 2.

#417 built ``publish.layers`` as a **daemon-local** gate (``.brr/config``)
over the seven dashboard-mirror lanes: real, but entirely client-side — an
operator's own promise to themself, enforced only by the daemon that reads
it (``brr.gates.cloud``). This module is the second half: an explicit
consent captured *at repo connect* on brnrd.dev, stored on the ``Repo`` row,
and checked again at the one seam that actually decides what reaches
brnrd.dev — the daemon's ``PUT /v1/daemons/*``. Two independent gates
narrowing the same surface, same as #417 argued for the daemon side: a UI
control alone is not enforcement.

Reuses ``brr.gates.cloud``'s parser and vocabulary rather than re-deriving
it — one definition of the scope grammar, not two that can drift. That was
the exact defect #417 closed for the daemon's own six lanes; inventing a
second parser here would reopen it one layer up.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from brr.gates.cloud import (
    _PUBLISH_CORPUS_SLICES as CORPUS_SLICES,
    _PUBLISH_OFF as OFF,
    _PUBLISH_TICK_ORDER as LANES,
    _resolve_publish_scopes,
)

from .models import Repo

# New connects consent explicitly and the product default is off — never the
# daemon-side "absent means everything" rule, which is a legacy-config
# convenience, not a consent. See the ticket's "open product question": the
# default flips for new connects only; existing accounts are untouched
# (their repos simply carry no stored consent at all, so nothing here
# applies to them below).
DEFAULT_NEW_CONNECT = OFF

_KNOWN_TOKENS = frozenset(LANES) | frozenset(CORPUS_SLICES) | {OFF}


def normalize_publish_layers(raw: str | None) -> str:
    """Validate and canonicalize a consent string; 400 on any unknown token.

    The daemon-side parser fails *closed and silent* on a bad token, because
    there is no one there to hand a 4xx to. A connect-time consent choice
    comes straight from a person submitting a form, so the same mistake gets
    a loud rejection instead: ``publish_layers=totalnonsense`` must not be
    byte-identical to ``publish_layers=none`` the way it was for #417's
    daemon-side bug before the fix.
    """
    text = (raw or "").strip()
    if not text:
        return OFF
    tokens = [part.strip().lower().replace("-", "_") for part in text.split(",") if part.strip()]
    if not tokens:
        return OFF
    unknown = sorted(set(tokens) - _KNOWN_TOKENS)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown publish-scope token(s): {', '.join(unknown)}. "
                f"Valid scopes: {', '.join(sorted(_KNOWN_TOKENS))}."
            ),
        )
    if OFF in tokens:
        return OFF
    # Canonical de-duped order so the stored string is stable/comparable —
    # two consents naming the same set always compare equal.
    ordered = [t for t in LANES if t in tokens] + [t for t in CORPUS_SLICES if t in tokens]
    return ",".join(ordered)


def _repo_scopes(publish_layers: str | None) -> tuple[frozenset[str], frozenset[str]] | None:
    """(lanes, corpus slices) a stored consent value permits, or ``None``.

    ``None`` on the *value* means "no consent recorded" — a repo connected
    before this gate shipped. That is not the same as the string ``"none"``,
    which is a recorded, explicit opt-out. Only a recorded value is ever
    enforced; an unrecorded one leaves this repo's current behaviour
    untouched, exactly as the ticket asks.
    """
    if publish_layers is None:
        return None
    return _resolve_publish_scopes({"publish.layers": publish_layers})


def lane_permitted(db: Session, *, repo_id: str | None, lane: str) -> bool:
    """May this repo's daemon publish ``lane`` right now?

    Fails open (``True``) whenever there is nothing to enforce against — no
    repo_id on the token, an unknown repo, or a repo that never recorded a
    consent (legacy). Only a repo with a *recorded* consent is gated, and a
    recorded ``none``/subset gates every one of the six non-corpus lanes,
    mirroring the daemon-side ``@_publish_lane`` shape one hop server-side.
    """
    if not repo_id:
        return True
    repo = db.get(Repo, repo_id)
    if repo is None:
        return True
    scopes = _repo_scopes(repo.publish_layers)
    if scopes is None:
        return True
    lanes, _slices = scopes
    return lane in lanes


def corpus_slices_permitted(db: Session, account_id: str) -> frozenset[str] | None:
    """Corpus slices the account's connected repos jointly consent to.

    The corpus/knowledge mirror is account-wide by construction (one home,
    shared across every repo the account connects) — no single repo's
    consent can own it alone. So this enforces the *intersection* across
    every connected repo's recorded consent: never ship a slice unless every
    connected repo agreed to it. If any connected repo has not recorded a
    consent yet (legacy), or the account has no repos, this returns ``None``
    — unenforced, current behaviour untouched, exactly as it was before any
    repo in this account had a consent to check.
    """
    repos = list(db.execute(select(Repo).where(Repo.account_id == account_id)).scalars())
    if not repos:
        return None
    resolved = []
    for repo in repos:
        scopes = _repo_scopes(repo.publish_layers)
        if scopes is None:
            return None
        resolved.append(scopes[1])
    slices = frozenset.intersection(*resolved) if resolved else frozenset()
    return slices
