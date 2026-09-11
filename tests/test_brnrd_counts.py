"""Tests for scripts/brnrd_counts.py — the read-only user/account census.

The script lives under ``scripts/``, not ``src/``, so it is loaded by path
rather than imported as a package — the same shape any one-off ops script in
this tree needs since ``scripts/`` is not on the install path.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from brnrd.db import make_engine, make_session_factory  # noqa: E402
from brnrd.models import Account, Base, Daemon, Repo, Subscription  # noqa: E402


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "brnrd_counts.py"
    spec = importlib.util.spec_from_file_location("brnrd_counts", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


brnrd_counts = _load_script()


def _session():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)()


def _account(session, id_: str, *, deleted: bool = False) -> Account:
    account = Account(id=id_, github_id=id_, github_login=f"user-{id_}")
    if deleted:
        account.deleted_at = datetime.now(timezone.utc)
    session.add(account)
    return account


def _seed(session, now: datetime) -> None:
    _account(session, "a1")  # bare account: nothing else
    _account(session, "a2")  # has a repo, no daemon
    session.add(Repo(id="r1", account_id="a2", repo_full_name="octocat/hello",
                      repo_owner="octocat", repo_name="hello"))
    _account(session, "a3")  # daemon seen 1 day ago — inside both windows
    session.add(Daemon(id="d1", account_id="a3", token_id="t1", daemon_name="d",
                        last_seen_at=now - timedelta(days=1)))
    _account(session, "a4")  # daemon seen 20 days ago — 30d only
    session.add(Daemon(id="d2", account_id="a4", token_id="t2", daemon_name="d",
                        last_seen_at=now - timedelta(days=20)))
    _account(session, "a5")  # active subscription — paying
    session.add(Subscription(id="s1", account_id="a5", stripe_subscription_id="sub_1",
                              status=Subscription.STATUS_ACTIVE))
    _account(session, "a6")  # canceled subscription — not paying
    session.add(Subscription(id="s2", account_id="a6", stripe_subscription_id="sub_2",
                              status=Subscription.STATUS_CANCELED))
    _account(session, "a7", deleted=True)  # Art-17 tombstone — excluded from every row
    session.commit()


def test_counts_reflect_seeded_rows():
    session = _session()
    now = datetime.now(timezone.utc)
    _seed(session, now)

    values = {row.key: row.value for row in brnrd_counts.compute_counts(session, now=now)}

    assert values["accounts"] == 6  # a1..a6; a7 is erased
    assert values["accounts_with_repo"] == 1
    assert values["accounts_daemon_7d"] == 1
    assert values["accounts_daemon_30d"] == 2
    assert values["paying_accounts"] == 1
    assert values["repos"] == 1


def test_deleted_account_repo_would_also_be_excluded():
    """Belt-and-suspenders: even if a repo row somehow outlived its account
    (account_deletion.py deletes both together, but the count must not rely
    on that invariant holding forever), the live-account join drops it."""
    session = _session()
    _account(session, "ghost", deleted=True)
    session.add(Repo(id="r-ghost", account_id="ghost", repo_full_name="o/r",
                      repo_owner="o", repo_name="r"))
    session.commit()

    values = {row.key: row.value for row in brnrd_counts.compute_counts(session)}
    assert values["accounts"] == 0
    assert values["accounts_with_repo"] == 0
    assert values["repos"] == 1  # the raw repo row still exists and is still counted


def test_output_carries_no_identifying_data():
    session = _session()
    now = datetime.now(timezone.utc)
    _seed(session, now)

    rows = brnrd_counts.compute_counts(session, now=now)
    rendered = brnrd_counts.render_table(rows) + "\n" + brnrd_counts.render_json(rows)

    for needle in ("octocat", "hello", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "sub_1", "@"):
        assert needle not in rendered, f"{needle!r} leaked into script output"


def test_json_and_table_agree():
    session = _session()
    now = datetime.now(timezone.utc)
    _seed(session, now)

    rows = brnrd_counts.compute_counts(session, now=now)
    import json
    parsed = json.loads(brnrd_counts.render_json(rows))
    assert parsed == {row.key: row.value for row in rows}
