"""The trial follows a successful machine connection, once per account."""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from sqlalchemy import select
from brnrd import home_trial
from brnrd.models import Account
from _helpers import brnrd_account_headers, brnrd_client, brnrd_repo_and_daemon


def _status(client, headers):
    r = client.get('/v1/accounts/home-trial', headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_sign_in_and_account_reads_do_not_start_trial():
    client = brnrd_client()
    headers = brnrd_account_headers(client.app)
    assert _status(client, headers) == dict(status='not_started', started_at=None, ends_at=None, enforcement_enabled=False, household=False, subscribed=False)
    assert client.get('/v1/accounts/subscription', headers=headers).status_code == 200
    assert _status(client, headers)['started_at'] is None
    assert client.get('/v1/accounts/home-trial').status_code == 401


def test_pairing_starts_once_and_reconnect_and_second_machine_keep_dates():
    client = brnrd_client()
    account, daemon, _ = brnrd_repo_and_daemon(client)
    first = _status(client, account)
    assert first['status'] == 'active'
    start = datetime.fromisoformat(first['started_at'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(first['ends_at'].replace('Z', '+00:00'))
    assert end - start == timedelta(days=14)
    for name in ['computer-one', 'computer-one', 'computer-two']:
        r = client.post('/v1/daemons/register', headers=daemon, json={'daemon_name': name})
        assert r.status_code == 200, r.text
        assert _status(client, account) == first
    # A second full device-flow pairing (reinstall / another machine) also keeps it.
    second_account, _, _ = brnrd_repo_and_daemon(client)
    assert _status(client, second_account) == first
    other = brnrd_account_headers(client.app, github_id='456', login='other')
    assert _status(client, other)['status'] == 'not_started'
    # Browser-cookie access for /account; device credentials cannot read it.
    client.cookies.set('brnrd_session', account['Authorization'].removeprefix('Bearer '))
    assert _status(client, {}) == first
    client.cookies.clear()
    assert client.get('/v1/accounts/home-trial', headers=daemon).status_code == 403


@pytest.mark.parametrize('path,body', [
    ('/v1/daemons/register', {'daemon_name': 'existing-install'}),
    ('/v1/daemons/loom', {'v': 1}),
    ('/v1/daemons/loom/parts', {'keys': [], 'parts': {}}),
])
def test_existing_device_starts_on_successful_connect_only(path, body):
    client = brnrd_client()
    account, daemon, _ = brnrd_repo_and_daemon(client)
    with client.app.state.SessionLocal() as db:
        row = db.execute(select(Account)).scalar_one()
        row.home_trial_started_at = None  # existing account at deployment
        db.commit()
    if path.endswith('/register'):
        bad = client.post(path, headers=daemon, json={})
        send = client.post
    else:
        bad = client.put(path, headers=daemon, json={})
        send = client.put
    assert bad.status_code == 422
    assert _status(client, account)['status'] == 'not_started'
    r = send(path, headers=daemon, json=body)
    assert r.status_code == 200, r.text
    assert _status(client, account)['status'] == 'active'


@pytest.mark.parametrize('tier', [Account.TIER_FREE, Account.TIER_SUBSCRIBED])
def test_expired_clock_does_not_change_access_or_subscription(tier):
    client = brnrd_client()
    account, daemon, _ = brnrd_repo_and_daemon(client)
    with client.app.state.SessionLocal() as db:
        row = db.execute(select(Account)).scalar_one()
        row.tier = tier
        row.home_trial_started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=15)
        db.commit()
    before = client.get('/v1/accounts/subscription', headers=account).json()
    assert _status(client, account)['status'] == 'expired'
    assert _status(client, account)['enforcement_enabled'] is False
    assert client.post('/v1/daemons/register', headers=daemon, json={'daemon_name': 'reinstall'}).status_code == 200
    assert _status(client, account)['status'] == 'expired'
    assert client.get('/v1/accounts/subscription', headers=account).json() == before


def test_exact_expiry_boundary():
    start = datetime(2026, 11, 17)
    account = Account(home_trial_started_at=start)
    end = start.replace(tzinfo=timezone.utc) + timedelta(days=14)
    assert home_trial.status(account, now=end - timedelta(microseconds=1)).status == 'active'
    assert home_trial.status(account, now=end).status == 'expired'


def test_pending_and_approved_pairing_do_not_start_until_computer_collects_token():
    from _helpers import approve_pair

    client = brnrd_client()
    headers = brnrd_account_headers(client.app)
    repo = client.post('/v1/accounts/repos', headers=headers,
                       json={'repo_full_name': 'owner/trial'}).json()
    pair = client.post('/v1/accounts/pair').json()
    poll = f"/v1/accounts/pair/{pair['pair_code']}"
    assert client.get(poll, params={'poll_secret': pair['poll_secret']}).json()['status'] == 'pending'
    approve_pair(client, pair['pair_code'], {'repo_id': repo['repo_id'], 'approve_secret': pair['approve_secret']}, headers=headers)
    assert _status(client, headers)['status'] == 'not_started'
    assert client.get(poll, params={'poll_secret': 'wrong'}).status_code == 401
    assert _status(client, headers)['status'] == 'not_started'
    assert client.get(poll, params={'poll_secret': pair['poll_secret']}).json()['daemon_token']
    first = _status(client, headers)
    assert first['status'] == 'active'
    assert client.get(poll, params={'poll_secret': pair['poll_secret']}).status_code == 200
    assert _status(client, headers) == first
