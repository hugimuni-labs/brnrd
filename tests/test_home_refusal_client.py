"""The install's side of the home gate's refusal: one named state, paced by Retry-After, never a degraded step."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from brr.gates import cloud, relay_lock  # noqa: E402
from test_brnrd_home_access import NOW, _iso, _setup  # noqa: E402
from test_brnrd_loom_parts import PARTS, _gated  # noqa: E402
from test_loom_relay import home  # noqa: E402,F401


@pytest.fixture
def refused(monkeypatch):
    """A real app with the gate on and this account past its window; the cloud client's session wired to it."""
    client, account, daemon, repo = _setup(gate_from=_iso(NOW - timedelta(days=15)))
    token = daemon["Authorization"].split(" ", 1)[1]
    calls = []

    def request(method, url, *, headers, timeout, json=None, params=None, data=None):
        calls.append((method, url))
        return client.request(method, url.removeprefix("http://testserver"), json=json, params=params,
                              content=data, headers=headers)

    monkeypatch.setattr(cloud._SESSION, "request", request)
    monkeypatch.setattr(cloud, "_HELD_UNTIL", {})
    monkeypatch.setattr(cloud, "_HELD_HOME", {})
    monkeypatch.setattr(cloud, "_load_state_from_dir", lambda d: {"brnrd_url": "http://testserver", "token": token})
    return SimpleNamespace(client=client, token=token, calls=calls)


def test_a_refused_path_is_named_and_held_until_retry_after(refused):
    with pytest.raises(cloud.HomeSubscriptionNeeded) as first:
        cloud._request("http://testserver", "PUT", "/v1/daemons/loom", token=refused.token, json={"v": 1}, retry=False)
    assert first.value.status_code == 429
    assert first.value.account_url.endswith("/account") and "/account" in str(first.value)
    assert first.value.retry_after == refused.client.app.state.settings.home_gate_retry_after_s
    # Held: the second ask never reaches the server, and is still the named state.
    with pytest.raises(cloud.HomeSubscriptionNeeded) as second:
        cloud._request("http://testserver", "PUT", "/v1/daemons/loom", token=refused.token, json={"v": 1}, retry=False)
    assert len(refused.calls) == 1 and 0 < second.value.retry_after <= first.value.retry_after
    # A path the gate keeps open is not held by it.
    assert "account_id" in cloud._request("http://testserver", "GET", "/v1/daemons/whoami", token=refused.token)


def test_another_429_stays_a_plain_error():
    resp = SimpleNamespace(status_code=429, json=lambda: {"detail": {"reason": "install_ceiling"}})
    assert cloud.home_refusal(resp, 60) is None
    resp = SimpleNamespace(status_code=429, json=lambda: (_ for _ in ()).throw(ValueError("not json")))
    assert cloud.home_refusal(resp, 60) is None


def test_the_pusher_is_quiet_waits_out_retry_after_and_tells_once(refused, monkeypatch, tmp_path):
    from brr.loom.runtime import loom as loom_mod
    from brr.loom.runtime.board import Pusher

    told = []
    monkeypatch.setattr(loom_mod, "notice", lambda home, facts, kind, subject, what, can, **kw: told.append(
        (kind, subject, kw.get("passive"))))
    monkeypatch.setattr("brr.loom.runtime.ledger.read_facts", lambda home: [])
    monkeypatch.setattr("brr.loom.runtime.gates.state_dir", lambda root, config: tmp_path)
    hashes = {k: f"h-{k}" for k in PARTS}
    parts = SimpleNamespace(parts=dict(PARTS), hashes=hashes, changed=threading.Condition(), home=_gated(tmp_path))
    pusher = Pusher(parts)
    pusher._put(tmp_path, dict(PARTS), hashes)  # the worker's body, run here so no thread races the asserts
    assert pusher.failing is None  # no degraded step
    assert isinstance(pusher.refused, cloud.HomeSubscriptionNeeded)
    assert pusher.refused_until - time.monotonic() > refused.client.app.state.settings.home_gate_retry_after_s - 5
    for _ in range(3):
        pusher.step(None)  # inside Retry-After: no push, and one notice however many ticks
    assert pusher.worker is None
    assert len(told) == 1 and told[0][0] == "home-subscription" and told[0][2] is True
    assert len(refused.calls) == 1


def test_the_snapshot_push_is_quiet_on_the_refusal(refused, tmp_path):
    from brr.loom.runtime.snapshot import Publisher

    publisher = Publisher.__new__(Publisher)
    publisher._put(tmp_path, {"v": 1})
    assert publisher.failing is None and len(refused.calls) == 1


def test_the_relay_poll_says_the_refusal_once_and_waits_retry_after(home, tmp_path):
    import connector_support as relay

    stop, lines, waits = threading.Event(), [], []
    refusal = cloud.HomeSubscriptionNeeded("home subscription needed: subscribe at https://x/account",
                                           account_url="https://x/account", retry_after=900)

    class Client:
        polls = 0

        def pull(self, cursor):
            Client.polls += 1
            if Client.polls == 3:
                stop.set()
            raise refusal

    real_wait = stop.wait
    stop.wait = lambda s=None: (waits.append(s), real_wait(0))[1]
    relay.poll_forever(home, Client(), relay_lock.RelayLock(tmp_path / "acct", "loom"), stop,
                       log=lines.append)
    assert [line for line in lines if "subscribe" in line] == [f"relay: paused, {refusal}"]
    assert waits[-3:] == [900, 900, 900]
