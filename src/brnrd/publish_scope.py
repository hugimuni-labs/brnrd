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

import json
from datetime import datetime, timezone
from typing import Any, NamedTuple

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from brr.gates.cloud import (
    _PUBLISH_CORPUS_SLICES as CORPUS_SLICES,
    _PUBLISH_OFF as OFF,
    _PUBLISH_TICK_ORDER as LANES,
    _resolve_publish_scopes,
)

from .db import Base
from .models import Account, Repo

# New connects consent explicitly and the product default is off — never the
# daemon-side "absent means everything" rule, which is a legacy-config
# convenience, not a consent. This module now applies the same reading to a
# *missing* stored value: an unrecorded consent resolves to OFF everywhere
# below, so a repo carrying no consent publishes nothing until its owner
# records a scope. Existing NULL rows are not backfilled — they go dark, and
# the repos surface tells their owner why.
DEFAULT_NEW_CONNECT = OFF

_KNOWN_TOKENS = frozenset(LANES) | frozenset(CORPUS_SLICES) | {OFF}


class ReposWithoutPublishConsent(NamedTuple):
    """Connected repo labels split by why they permit no publish lanes.

    ``unrecorded_ids``/``opted_out_ids`` run parallel to ``unrecorded``/
    ``opted_out`` (same order, same length) rather than replacing the
    name-only tuples — the two lists were already a public contract
    (``test_consent_absence_distinguishes_unrecorded_from_explicit_none``
    asserts plain name tuples) and the dashboard consent popover is the
    first reader that needs the id half to act in place.
    """

    unrecorded: tuple[str, ...]
    opted_out: tuple[str, ...]
    unrecorded_ids: tuple[str, ...] = ()
    opted_out_ids: tuple[str, ...] = ()


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


def _repo_scopes(publish_layers: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """(lanes, corpus slices) a stored consent value permits.

    **Total by construction, and that is the point.** This used to return
    ``None`` for ``publish_layers is None`` ("no consent recorded" — a repo
    connected before this gate shipped, or one minted through the account
    API-key surface, which recorded nothing at all). Every caller then had to
    remember what to do with that ``None``, and two of the three chose
    "publish everything": an unrecorded consent was silently the most
    permissive state in the system.

    Unrecorded is not permission. A missing value now resolves to ``OFF`` —
    the same scopes as a recorded, explicit opt-out — so the *type* no longer
    carries a way to say "unenforced", and no caller can reintroduce the
    fail-open by forgetting a branch. The repo goes dark until its owner
    records a scope; the surface says so (``publishScopeSummary`` in
    ``src/frontend/src/lib/publishScope.ts``).

    Existing ``NULL`` rows are deliberately **not** backfilled: writing a
    consent nobody gave would fabricate exactly the evidence this gate
    exists to hold.
    """
    if publish_layers is None:
        return _resolve_publish_scopes({"publish.layers": OFF})
    return _resolve_publish_scopes({"publish.layers": publish_layers})


def _purge_targets() -> list[tuple[str, str, type, str | None]]:
    """Persisted publish stores, discovered from their model metadata.

    A model-level marker describes a repo-keyed row store such as
    ``ActivityRecord``.  Column markers describe JSON/scalar mirror fields.
    The canonical lane vocabulary remains ``LANES``; this discovery is
    deliberately checked against it so a newly registered publisher cannot
    exist without a withdrawal target.
    """
    targets: list[tuple[str, str, type, str | None]] = []
    for mapper in Base.registry.mappers:
        model = mapper.class_
        lane = getattr(model, "__publish_lane__", None)
        purge = getattr(model, "__publish_purge__", None)
        if isinstance(lane, str) and isinstance(purge, str):
            targets.append((lane, purge, model, None))
        for column in mapper.columns:
            lane = column.info.get("publish_lane")
            purge = column.info.get("publish_purge")
            if isinstance(lane, str) and isinstance(purge, str):
                targets.append((lane, purge, model, column.key))
    return targets


def purge_storage_lanes() -> frozenset[str]:
    """Canonical publish lanes for which persisted purge targets exist.

    Raises when discovery and the canonical vocabulary disagree, so a new
    publisher cannot ship without a withdrawal target. **This is a
    development-time assertion and it is deliberately not called on the
    withdrawal path** — see ``purge_removed_scope``. A user must never be
    unable to withdraw consent because *we* forgot to mark a store.
    """
    targets = _purge_targets()
    covered = frozenset(lane for lane, _purge, _model, _field in targets)
    canonical = frozenset(LANES)
    if covered != canonical:
        missing = sorted(canonical - covered)
        unknown = sorted(covered - canonical)
        raise RuntimeError(
            "publish purge storage does not match the canonical lane vocabulary: "
            f"missing={missing}, unknown={unknown}"
        )
    return covered


def _transition_scopes(value: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """Scopes whose stored copies ``value`` currently permits.

    ``_repo_scopes`` is total and reads legacy ``NULL`` as ``OFF``. Recording
    a scope on an old row is therefore a widening from nothing, not a
    narrowing from the permissive state that existed before #778.
    """
    return _repo_scopes(value)


_UNPARSEABLE = object()


def _json_list(raw: Any) -> list[Any] | object:
    """Stored mirror content as a list, or ``_UNPARSEABLE``.

    **Never raises.** An earlier shape answered malformed stored JSON with a
    409 *"publish scope was not changed"* — which makes our own corrupt render
    cache a reason the user cannot withdraw their consent, and that is the one
    direction this whole module exists to forbid (GDPR Art 7(3): withdrawal at
    least as easy as consent). The caller treats this sentinel as *erase the
    whole field*: on a withdrawal the safe error is always to delete more.
    Over-erasing a replaceable cache costs one republish; under-erasing is the
    violation.
    """
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return _UNPARSEABLE
    if not isinstance(value, list):
        return _UNPARSEABLE
    return value


def _column_reset_value(column) -> Any:
    default = column.default
    if default is None or callable(default.arg):
        return None
    return default.arg


def purge_removed_scope(
    db: Session,
    *,
    repo: Repo,
    new_publish_layers: str,
) -> tuple[frozenset[str], frozenset[str]]:
    """Remove stored mirror content covered by a narrowing consent change.

    This function never commits.  The caller writes ``Repo.publish_layers``
    and commits once after this returns, making the erasure and the consent
    record one transaction.

    Repo-keyed stores are cleared only for this repo.  Account-wide row
    snapshots are filtered by their ``repo_label`` and retain rows with no
    attributable subject.  The corpus has no per-repo attribution at all;
    its safe coordinate is the file's canonical ``layer``, so only removed
    corpus slices are filtered and every other slice survives.
    """
    old_lanes, old_slices = _transition_scopes(repo.publish_layers)
    new_lanes, new_slices = _transition_scopes(new_publish_layers)
    removed_lanes = old_lanes - new_lanes
    removed_slices = old_slices - new_slices
    if not removed_lanes and not removed_slices:
        return frozenset(), frozenset()

    # Deliberately NOT `purge_storage_lanes()` here. That assertion is a
    # development-time guarantee (see its docstring and the coverage test); at
    # runtime it would mean a lane someone forgot to mark makes the *user*
    # unable to withdraw. Purge every target we can discover and let the
    # consent write land regardless.
    affected_lanes = set(removed_lanes)
    if removed_slices:
        affected_lanes.add("corpus")

    targets = [
        target
        for target in _purge_targets()
        if target[0] in affected_lanes
    ]
    wanted_label = repo.repo_full_name.casefold()
    surface_changed = False

    for lane, purge, model, field in targets:
        if field is None:
            # Relational row store: ActivityRecord today, and any future
            # repo-FK publish store that carries the same model marker.
            db.execute(delete(model).where(model.repo_id == repo.id))
            continue

        column = model.__table__.columns[field]
        if purge == "repo":
            rows = db.execute(select(model).where(model.repo_id == repo.id)).scalars()
            for row in rows:
                setattr(row, field, _column_reset_value(column))
            continue

        if purge == "repo_label":
            rows = db.execute(
                select(model).where(model.account_id == repo.account_id)
            ).scalars()
            for row in rows:
                stored = _json_list(getattr(row, field))
                if stored is _UNPARSEABLE:
                    # Cannot tell whose rows these are ⇒ erase the field. See
                    # `_json_list`: on a withdrawal the safe error is to delete
                    # more, never to refuse.
                    setattr(row, field, _column_reset_value(column))
                    continue
                kept = [
                    item
                    for item in stored
                    if not (
                        isinstance(item, dict)
                        and isinstance(item.get("repo_label"), str)
                        and item["repo_label"].casefold() == wanted_label
                    )
                ]
                if kept != stored:
                    setattr(row, field, json.dumps(kept, separators=(",", ":")))
            continue

        if purge == "slice":
            if lane != "corpus" or not removed_slices:
                continue
            account = db.get(Account, repo.account_id)
            if account is None:
                continue
            stored = _json_list(getattr(account, field))
            if stored is _UNPARSEABLE:
                setattr(account, field, _column_reset_value(column))
                surface_changed = True
                continue
            kept = [
                item
                for item in stored
                if not (
                    isinstance(item, dict)
                    and item.get("layer") in removed_slices
                )
            ]
            if kept != stored:
                setattr(account, field, json.dumps(kept, separators=(",", ":")))
                surface_changed = True
            continue

        raise RuntimeError(f"unknown publish purge strategy {purge!r}")

    if surface_changed:
        account = db.get(Account, repo.account_id)
        if account is not None:
            account.surface_updated_at = datetime.now(timezone.utc)
    return frozenset(removed_lanes), frozenset(removed_slices)


#: Lanes whose payload is sliced, and therefore gated by the account-wide
#: *intersection* in ``corpus_slices_permitted`` rather than by the per-repo
#: lane check in ``lane_permitted``. Named here so ``lanes_withheld`` and the
#: write-side gate cannot answer the same question with different reductions;
#: ``test_sliced_lanes_matches_slice_enforcement`` pins the membership, so a
#: second sliced lane fails a test instead of silently inheriting the union.
SLICED_LANES = frozenset({"corpus"})


def lanes_withheld(repos: list[Repo]) -> frozenset[str]:
    """Lanes where an empty payload is certainly consent, not absence.

    A repo-scoped lane is withheld only when no connected repo in the account
    permits it. One permitting repo keeps an empty payload ambiguous: that repo
    may simply be idle. With zero connected repos there is no consent question
    and thus no provable claim, so this returns an empty set.

    **A sliced lane is not that question.** The corpus is account-wide by
    construction — one shared home — so it is enforced by the *intersection*
    of every connected repo's slices (``corpus_slices_permitted``), not by any
    single repo's yes. Answering it with the same union as the six repo-scoped
    lanes made this function disagree with the gate it describes: an account
    with one fully-consented repo and one unrecorded repo had every file
    dropped at the write seam while this said "not withheld", so the dashboard
    rendered a bare "No corpus mirrored yet." — the one sentence that is false
    in exactly the case the marker exists for. Ask the reduction that runs.

    Takes the already-loaded repo list rather than a ``Session``: this is asked
    once per dashboard lane per poll, the dashboard polls at 2 s, and six of the
    seven callers have the same list in scope already. A convenience signature
    that re-queries would have cost a round trip per lane per tick forever, to
    answer "no" in the overwhelmingly common case.
    """
    if not repos:
        return frozenset()
    scopes = [_repo_scopes(repo.publish_layers) for repo in repos]
    permitted = frozenset().union(*(lanes for lanes, _slices in scopes))
    withheld = set(LANES) - permitted
    if not frozenset.intersection(*(slices for _lanes, slices in scopes)):
        withheld |= SLICED_LANES
    return frozenset(withheld)


def repos_without_publish_consent(repos: list[Repo]) -> ReposWithoutPublishConsent:
    """Repos that were never asked, and repos that explicitly chose nothing.

    ``None`` and ``'none'`` resolve to the same *scopes* and to two very
    different sentences: one is "we never asked you", the other is "you told
    us no". Same list-in, list-out contract as ``lanes_withheld``.
    """
    unrecorded = sorted(
        ((repo.repo_full_name, repo.id) for repo in repos if repo.publish_layers is None),
        key=lambda pair: pair[0].casefold(),
    )
    opted_out = sorted(
        ((repo.repo_full_name, repo.id) for repo in repos if repo.publish_layers == OFF),
        key=lambda pair: pair[0].casefold(),
    )
    return ReposWithoutPublishConsent(
        unrecorded=tuple(name for name, _id in unrecorded),
        opted_out=tuple(name for name, _id in opted_out),
        unrecorded_ids=tuple(id_ for _name, id_ in unrecorded),
        opted_out_ids=tuple(id_ for _name, id_ in opted_out),
    )


def lane_permitted(db: Session, *, repo_id: str | None, lane: str) -> bool:
    """May this repo's daemon publish ``lane`` right now?

    A repo that never recorded a consent now publishes **nothing** — the
    unrecorded case resolves to ``OFF`` inside ``_repo_scopes`` rather than
    being special-cased to ``True`` here. A recorded ``none``/subset gates
    every one of the six non-corpus lanes, mirroring the daemon-side
    ``@_publish_lane`` shape one hop server-side.

    Two ``True`` returns remain, and neither is a consent question: a token
    carrying no ``repo_id`` at all, and a ``repo_id`` naming no row. Those
    are "this payload has no repo to ask about" — the daemon-scoped lanes
    (``activity``, ``quota``, ``runners``) reach here that way legitimately.
    They are a genuinely separate gap from the one this docstring used to
    describe, and are left as-is rather than widened into silently.
    """
    if not repo_id:
        return True
    repo = db.get(Repo, repo_id)
    if repo is None:
        return True
    lanes, _slices = _repo_scopes(repo.publish_layers)
    return lane in lanes


def _subject_repos(repos: list[Repo], repo_label: str) -> list[Repo]:
    """Every repo in the account a row's ``repo_label`` could be naming.

    Matched **case-insensitively**, because the two sides of this comparison
    have different provenance and only one of them is authoritative about
    case:

    - the ``Repo`` row records what the connect payload / GitHub API said
      (`routers/accounts.py`);
    - the daemon derives ``repo_label`` by parsing the git remote URL
      (`brr.gates.cloud::_github_repo_label` -> ``parse_origin_url``, with a
      further fallback to the checkout's directory name).

    GitHub serves repositories case-insensitively, so a clone of
    ``.../gurio/brnrd`` yields a remote that parses to ``gurio/brnrd`` while
    the connect record says ``Gurio/brnrd``. An exact comparison silently
    fails to resolve there — and a subject that fails to resolve falls back
    to the publisher, which is #714's own defect wearing one character of
    case.

    The codebase had already reached this conclusion twice and this module
    was the odd one out: `routers/webhooks.py::_find_repo` matches on
    ``casefold()``, and this lane's *own producer* dedups its labels by
    ``repo_label.casefold()`` (`cloud.py::_pr_review_repo_labels`). The
    producer folded and the consumer did not.

    Returns a **list**, not a single row, and that is deliberate — see
    ``_subject_permits``. ``Repo`` is unique on
    ``(account_id, repo_full_name)`` (`models.py:46`), but that constraint is
    case-*sensitive* and so is the dedup in
    `routers/accounts.py::create_repo`, so one account can legitimately hold
    ``Gurio/x`` and ``gurio/x`` as two rows carrying two different consents.
    """
    wanted = repo_label.casefold()
    return [repo for repo in repos if repo.repo_full_name.casefold() == wanted]


def _subject_permits(
    db: Session,
    repos: list[Repo],
    *,
    repo_label: str | None,
    publisher_repo_id: str | None,
    lane: str,
) -> bool:
    """May this one row publish, judged on the repo it is *about*?

    Three cases, and the middle one is the reason this is not a lookup:

    - **No match** — the row names no repo, or names one this account has not
      connected. Falls back to the **publisher's** consent, which is exactly
      the question asked before #714 existed: the unresolvable case stays
      byte-for-byte at its old behaviour, and a spoofed label cannot buy a
      wider audience than the token that carried it.
    - **Several matches** — case variants of one name, each with its own
      recorded consent. The row publishes only if **every** one of them
      permits the lane. This is #715's rule (`corpus_slices_permitted`)
      pointed at a new axis: *enforcement must not weaken when a repo is
      added*. Resolving the ambiguity by giving up — `_find_repo`'s
      ``len(matches) == 1`` shape — would fall through to the publisher and
      make a second connect *widen* what the first one had shut.
    - **One match** — that repo's consent, the ordinary case.

    A repo with no recorded consent among the matches now *vetoes*, because
    ``lane_permitted`` reads an unrecorded value as ``OFF``. It used to
    return ``True`` there — neither consent nor veto — which meant one
    unconsented case-variant could not stop a publish, and adding it to the
    account changed nothing.
    """
    label = (repo_label or "").strip()
    matches = _subject_repos(repos, label) if label else []
    if not matches:
        return lane_permitted(db, repo_id=publisher_repo_id, lane=lane)
    return all(lane_permitted(db, repo_id=repo.id, lane=lane) for repo in matches)


def permitted_rows(
    db: Session,
    rows: list,
    *,
    account_id: str,
    publisher_repo_id: str | None,
    lane: str,
) -> list:
    """Filter ``rows`` to the ones their *own* repo consents to publishing.

    Only for lanes whose payload rows name a repo (``live_runs``,
    ``pr_review_queue``, ``run_ledger``). The daemon-scoped lanes —
    ``activity``, ``quota``, ``runners`` — carry no per-row subject, so
    ``lane_permitted`` on the token's repo is the *correct* question there
    and they keep asking it. Reusing one predicate for uniformity is what
    put the subject and the publisher on the same key to begin with.

    Filters, never rejects: a mixed payload keeps its permitted rows and
    drops the rest, the same shape as the ``rows if permitted else []`` it
    replaces.

    The candidate set is read once per request and matched in Python rather
    than per row in SQL: the match is ``casefold()`` (see ``_subject_repos``),
    which is a Python string operation the database's own collation cannot be
    trusted to reproduce, and an account's repo list is small — the same
    shape `corpus_slices_permitted` and `routers/webhooks.py::_find_repo`
    already use. Each distinct label is then decided once.
    """
    if not rows:
        return []
    # Scoped to the token's account: `repo_label` is client-supplied, and a
    # daemon must not be able to name another account's repo and borrow its
    # consent.
    repos = list(db.execute(select(Repo).where(Repo.account_id == account_id)).scalars())
    decided: dict[str, bool] = {}
    kept = []
    for row in rows:
        label = (getattr(row, "repo_label", None) or "").strip()
        if label not in decided:
            decided[label] = _subject_permits(
                db,
                repos,
                repo_label=label,
                publisher_repo_id=publisher_repo_id,
                lane=lane,
            )
        if decided[label]:
            kept.append(row)
    return kept


def corpus_slices_permitted(db: Session, account_id: str) -> frozenset[str] | None:
    """Corpus slices the account's connected repos jointly consent to.

    The corpus/knowledge mirror is account-wide by construction (one home,
    shared across every repo the account connects) — no single repo's
    consent can own it alone. So this enforces the *intersection* across
    every connected repo that has **recorded** a consent: never ship a slice
    unless every one of them agreed to it.

    A repo with no recorded value no longer abstains: it contributes ``OFF``
    like any other unrecorded consent, so a single unconsented repo narrows
    the account's corpus to nothing rather than being skipped. That is the
    intended direction of #715's rule — *enforcement must not weaken when a
    repo is added* — now applied to the unrecorded case too, which used to
    be the one row that could be added without ever narrowing anything.

    ``None`` still means "nothing to enforce against", but that is now
    reachable only when the account has **no connected repos at all**: with
    no repo there is no consent question to ask. Note that this remains a
    fail-open for an account whose corpus mirrors with zero repos connected
    — a separate gap from the one this function just closed, called out
    rather than quietly widened.
    """
    repos = list(db.execute(select(Repo).where(Repo.account_id == account_id)).scalars())
    resolved = [_repo_scopes(repo.publish_layers)[1] for repo in repos]
    if not resolved:
        return None
    return frozenset.intersection(*resolved)
