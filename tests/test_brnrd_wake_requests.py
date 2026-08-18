"""Unit tests for `brnrd.wake_requests.claim` (#1492, starved-tap half).

Deliberately below the HTTP layer that `tests/test_brnrd_runners.py` already
covers: these build real `RunnerWakeRequest` rows against an in-memory
sqlite session and call `claim()` directly, so a failure here points at the
guard ladder itself rather than routing, auth, or schema plumbing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from brnrd import ids, wake_requests  # noqa: E402
from brnrd.models import Account, Base, RunnerWakeRequest  # noqa: E402

# The AGENTS.md trap this file is written against: a bare import inside a
# worktree can silently resolve to the host checkout's installed copy
# instead of this tree's. Assert identity rather than trusting the path.
assert Path(wake_requests.__file__).resolve().is_relative_to(
    Path(__file__).resolve().parents[1]
), f"wake_requests imported from {wake_requests.__file__}, not this tree"


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _account(db) -> str:
    account = Account(id=ids.account_id(), github_id="gh-1", github_login="octocat")
    db.add(account)
    db.commit()
    return account.id


def _tap(db, account_id: str, *, created_at: datetime, profile: str = "codex") -> RunnerWakeRequest:
    row = RunnerWakeRequest(
        id=ids.runner_wake_request_id(),
        account_id=account_id,
        profile=profile,
        status=RunnerWakeRequest.STATUS_PENDING,
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=wake_requests.WAKE_REQUEST_TTL_S),
    )
    db.add(row)
    db.commit()
    return row


def test_healthy_refusal_still_refuses_a_freshly_parked_tap():
    """The correctness rung the task guardrails name: a tap parked *after*
    an event already existed must not consume that event's wake, in the
    ordinary single-shot case."""
    db = _session()
    account_id = _account(db)
    now = datetime.now(timezone.utc)
    tap = _tap(db, account_id, created_at=now)

    verdict = wake_requests.claim(
        db,
        account_id,
        request_id=tap.id,
        source="telegram",
        event_created=now - timedelta(minutes=5),
        daemon_now=now,
    )

    assert verdict["apply"] is False
    assert "parked after" in verdict["reason"]
    assert verdict["status"] == "pending"

    row = db.get(RunnerWakeRequest, tap.id)
    assert row.status == RunnerWakeRequest.STATUS_PENDING
    assert row.parked_after_refusals == 1
    assert row.blocked_reason == verdict["reason"]
    # Visible on the account's tap-parked-here surface too, not just the
    # response to the daemon that made the (losing) claim.
    assert wake_requests.view(row)["blocked_reason"] == verdict["reason"]


def test_starved_tap_releases_after_the_refusal_bound_tonight_shaped():
    """The tonight-shaped scenario from #1492: a tap parked at T, an event
    created before T, and that event re-queued and still pending hours
    later — so the same blocking event refuses the same tap repeatedly.
    Past `PARKED_AFTER_REFUSAL_BOUND` refusals the tap must stop losing."""
    db = _session()
    account_id = _account(db)
    t = datetime.now(timezone.utc)
    tap = _tap(db, account_id, created_at=t)
    event_created = t - timedelta(minutes=24)  # older than the tap

    verdicts = []
    for hop in range(wake_requests.PARKED_AFTER_REFUSAL_BOUND + 1):
        daemon_now = t + timedelta(hours=hop)  # the re-queued event keeps surviving
        verdicts.append(
            wake_requests.claim(
                db,
                account_id,
                request_id=tap.id,
                source="telegram",
                event_created=event_created,
                daemon_now=daemon_now,
            )
        )

    # Every refusal up to the bound: still refused, still pending.
    for verdict in verdicts[: wake_requests.PARKED_AFTER_REFUSAL_BOUND]:
        assert verdict["apply"] is False
        assert "parked after" in verdict["reason"]

    # The refusal that pushes past the bound applies instead — the same
    # event, the same age relationship, but the tap has waited long enough.
    final = verdicts[-1]
    assert final["apply"] is True, final.get("reason")
    assert final["status"] == "consumed"

    row = db.get(RunnerWakeRequest, tap.id)
    assert row.status == RunnerWakeRequest.STATUS_CONSUMED
    assert row.parked_after_refusals == wake_requests.PARKED_AFTER_REFUSAL_BOUND
    assert row.blocked_reason is None
    # A decided row's story lives in the response, not the mirror.
    assert wake_requests.view(row)["blocked_reason"] is None


def test_missing_timestamp_skips_the_rung_without_dropping_the_tap():
    """`:242`'s forgiving guard, unchanged: any of the three stamps absent
    means the age rung is skipped, not that the tap silently vanishes."""
    db = _session()
    account_id = _account(db)
    now = datetime.now(timezone.utc)
    tap = _tap(db, account_id, created_at=now)

    verdict = wake_requests.claim(
        db,
        account_id,
        request_id=tap.id,
        source="telegram",
        event_created=now - timedelta(minutes=5),
        daemon_now=None,  # no daemon clock ⇒ the rung cannot be judged
    )

    assert verdict["apply"] is True
    assert verdict["reason"] is None

    row = db.get(RunnerWakeRequest, tap.id)
    assert row.status == RunnerWakeRequest.STATUS_CONSUMED
    # The skipped rung never ran, so it never touched the counter either.
    assert row.parked_after_refusals == 0


def test_schedule_source_still_defers_and_now_records_why():
    """#564's rule is untouched by the bound (it lives on a different rung);
    this only checks the new `blocked_reason` stamp rides along."""
    db = _session()
    account_id = _account(db)
    now = datetime.now(timezone.utc)
    tap = _tap(db, account_id, created_at=now)

    verdict = wake_requests.claim(db, account_id, request_id=tap.id, source="schedule")

    assert verdict["apply"] is False
    assert "schedule" in verdict["reason"]
    row = db.get(RunnerWakeRequest, tap.id)
    assert row.status == RunnerWakeRequest.STATUS_PENDING
    assert row.blocked_reason == verdict["reason"]
