"""The account cloud loop and its consumers must use the same runtime."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from brr import account, daemon, protocol, wake_request
from brr.gates import cloud, runtime


def write_health(brr_dir, gate, data):
    path = runtime.health_path(brr_dir, gate)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def context(tmp_path):
    launch = tmp_path / 'launch'
    anchor = tmp_path / 'default'
    home = tmp_path / 'home'
    for root in (launch, anchor):
        (root / '.brr').mkdir(parents=True)
    repos = {label: account.AccountRepo(label=label, root=root)
             for label, root in [('o/launch', launch), ('o/default', anchor)]}
    ctx = account.AccountContext(
        account_id='test', dominion_repo=home, dispatch_inbox=home / 'inbox',
        responses_dir=home / 'responses', runs_dir=home / 'runs', repos=repos,
        default_repo=repos['o/default'], kind='account', home_root=home,
    )
    return launch, anchor, home, ctx


def test_cloud_tap_uses_the_runtime_started_for_the_account(tmp_path, monkeypatch):
    launch, anchor, home, ctx = context(tmp_path)
    started = []
    monkeypatch.setattr(daemon, '_start_gates', lambda *args: started.append(args) or [])
    daemon._start_account_gates(ctx, launch)
    assert started[-1][0] == anchor / '.brr'
    wake_request.store_pending(anchor / '.brr', {'request_id': 'wake_opus'})
    path = protocol.create_event(ctx.dispatch_inbox, source='cloud', body='recover')
    event = protocol._read_event(path)
    target = daemon._DispatchTarget(event, launch, ctx.dispatch_inbox,
                                    ctx.responses_dir, 'o/launch')
    claims = []
    def claim(brr_dir, **kwargs):
        claims.append((brr_dir, kwargs))
        return {'request_id': 'wake_opus', 'status': 'consumed',
                'apply': True, 'profile': 'claude-opus'}
    monkeypatch.setattr(cloud, 'claim_wake_request', claim)
    monkeypatch.setattr(daemon.conf, 'write_daemon_config', lambda *a, **k: home / 'config')
    daemon._apply_dashboard_wake_request(target, ctx, launch)
    assert len(claims) == 1
    assert claims[0][0] == anchor / '.brr'
    assert event['runner'] == 'claude-opus'
    assert wake_request.sticky_record(anchor / '.brr')['profile'] == 'claude-opus'
    assert wake_request.pending_id(anchor / '.brr') is None
    daemon._apply_dashboard_wake_request(target, ctx, launch)
    assert len(claims) == 1


@pytest.mark.parametrize('delivery_error', [None, 'message rejected'])
def test_cloud_health_reads_live_anchor_and_account_delivery(tmp_path, monkeypatch, delivery_error):
    launch, anchor, home, ctx = context(tmp_path)
    monkeypatch.setattr(account, 'resolve_context', lambda *a, **k: ctx)
    now = datetime.now(timezone.utc)
    write_health(launch / '.brr', 'cloud', {
        'last_poll_ok': (now - timedelta(days=1)).isoformat(),
        'last_error': 'old network error', 'last_error_at': now.isoformat(),
    })
    write_health(anchor / '.brr', 'cloud', {'last_poll_ok': now.isoformat()})
    write_health(home / 'account', 'cloud', {
        'delivery_error': delivery_error, 'delivery_attempts': 3 if delivery_error else 0,
    })
    write_health(launch / '.brr', 'telegram', {'last_poll_ok': now.isoformat()})
    rows = runtime.gate_health_rows(launch / '.brr', gates=['cloud', 'telegram'], now=now)
    cloud_row, telegram_row = rows
    assert cloud_row['age_seconds'] == 0
    assert cloud_row['last_error'] is None
    assert cloud_row['status'] == ('degraded' if delivery_error else 'ok')
    assert cloud_row['delivery_error'] == delivery_error
    assert cloud_row['delivery_attempts'] == (3 if delivery_error else 0)
    assert telegram_row['status'] == 'ok'
