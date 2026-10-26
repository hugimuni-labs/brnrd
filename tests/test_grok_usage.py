"""Quota values grounded in captured Grok Build 1.0.50 PTY bytes."""

import json
import os
import time
from pathlib import Path

import pytest

from brr import daemon, facets, grok_status, grok_usage, hooks, runner_quota
from brr.gates import cloud

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _grok_on_path(monkeypatch):
    """Tests drive a fake capture; pretend the binary exists unless a test says not."""
    monkeypatch.setattr(grok_usage, "_grok_installed", lambda env=None: True)


def screen():
    return (FIXTURES / "grok_usage_screen_exhausted.txt").read_bytes()


def billing():
    return (FIXTURES / "grok_billing_2026_10_17.json").read_bytes()


def test_daemon_merges_quota_preserving_own_tokens_and_read_only_boundaries(tmp_path, monkeypatch):
    shared, outbox = tmp_path / "shared", tmp_path / "outbox"
    usage = grok_usage.parse_usage_text(screen())
    grok_usage.write_snapshot(shared, usage)
    result = {"source": "grok result JSON", "run_id": "own",
              "tokens": {"input_tokens": 10},
              "spend": {"summary": "$0.42 this session"}}
    grok_status.write_snapshot(outbox, result)
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: pytest.fail("read-only boundary probed"))
    levels, slots = daemon._collect_levels("grok-4.7", outbox, shared_dir=shared, refresh=False)
    assert slots == {"spend", "quota"}
    assert levels["quota"]["buckets"]["week"]["remaining_percentage"] == 0.0
    assert levels["tokens"]["input_tokens"] == 10 and levels["run_id"] == "own"
    assert levels["spend"]["summary"] == "$0.42 this session"
    assert levels["quota"]["updated_at"] == usage["updated_at"]
    projected = facets.build(levels=levels, levels_collector=slots)
    assert projected["quota"]["status"] == "known"
    reset_clause = hooks._QUOTA_RESET_RE.search(projected["quota"]["summary"])
    assert hooks._relative_reset(reset_clause["when"]) is not None
    assert hooks._quota_chip(projected).startswith("q W0↻")


def test_daemon_cold_shared_cache_and_cross_run_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: billing())
    grok_status.write_snapshot(tmp_path, {
        "source": "grok result JSON", "tokens": {"input_tokens": 99},
        "spend": {"summary": "$0.42 this session"},
    })
    levels, slots = daemon._collect_levels("grok", None, shared_dir=tmp_path)
    assert grok_usage.load_snapshot(tmp_path)["week_used_percentage"] == 55.0
    assert "tokens" not in levels
    assert "previous Grok session" in levels["spend"]["summary"]
    assert runner_quota.binding_quota_remaining_pct(levels) == 45.0


def test_publisher_reads_a_fresh_cache_without_reprobing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: calls.append(kw) or billing())
    cloud._grok_quota_shell(tmp_path)
    old = time.time() - 300  # inside the 600 s publish window
    os.utime(tmp_path / grok_usage.SNAPSHOT_NAME, (old, old))
    cloud._grok_quota_shell(tmp_path)
    assert len(calls) == 1
    from brr.gates import cloud_publisher
    assert cloud_publisher._GROK_QUOTA_PUBLISH_MAX_AGE_SECONDS >= 600
    assert grok_usage.DEFAULT_TTL_SECONDS >= 600


def auth_env(tmp_path, *, expired=False):
    auth = {grok_usage.AUTH_SCOPE: {
        "key": "test-read-only-key", "user_id": "test-user",
        "expires_at": "2000-01-01T00:00:00Z" if expired else "2099-01-01T00:00:00Z",
        "refresh_token": "must-never-be-used",
    }}
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    return {"GROK_HOME": str(tmp_path), "PATH": str(tmp_path)}, path


def test_http_reader_never_starts_grok_or_changes_auth(tmp_path, monkeypatch, _no_grok_usage_pty_scrape):
    import io
    env, path = auth_env(tmp_path)
    marker = tmp_path / "executed"
    binary = tmp_path / "grok"
    binary.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
    binary.chmod(0o755)
    before = path.read_bytes()
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            return io.BytesIO(billing())

    monkeypatch.setattr(grok_usage.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(grok_usage, "capture_usage_raw", _no_grok_usage_pty_scrape)
    levels = grok_usage.capture_levels(env=env)
    assert levels["week_used_percentage"] == 55
    assert levels["week_resets_at"] == 1792411195.857848
    assert calls[0].full_url == grok_usage.BILLING_URL
    assert calls[0].get_method() == "GET"
    assert calls[0].get_header("Authorization") == "Bearer test-read-only-key"
    assert not marker.exists()
    assert path.read_bytes() == before


@pytest.mark.parametrize("state", ["expired", "absent", "invalid-expiry"])
def test_unusable_access_key_is_absent_with_error_without_http(tmp_path, monkeypatch, _no_grok_usage_pty_scrape, state):
    env, path = auth_env(tmp_path, expired=state == "expired")
    if state == "absent":
        path.unlink()
    elif state == "invalid-expiry":
        data = json.loads(path.read_text())
        del data[grok_usage.AUTH_SCOPE]["expires_at"]
        path.write_text(json.dumps(data))
    before = path.read_bytes() if path.exists() else None
    monkeypatch.setattr(grok_usage, "capture_usage_raw", _no_grok_usage_pty_scrape)
    monkeypatch.setattr(grok_usage.urllib.request, "build_opener", lambda *a: pytest.fail("unusable key made HTTP request"))
    levels = grok_usage.capture_levels(env=env)
    assert "quota" not in levels and "week_used_percentage" not in levels
    assert "expired" in levels["error"] if state == "expired" else levels["error"]
    assert (path.read_bytes() if path.exists() else None) == before


def test_http_401_does_not_refresh_or_retry(tmp_path, monkeypatch, _no_grok_usage_pty_scrape):
    import urllib.error
    env, path = auth_env(tmp_path)
    before = path.read_bytes()
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(grok_usage.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(grok_usage, "capture_usage_raw", _no_grok_usage_pty_scrape)
    levels = grok_usage.capture_levels(env=env)
    assert calls == [grok_usage.BILLING_URL]
    assert "401" in levels["error"] and "quota" not in levels
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("creditUsagePercent", None), ("creditUsagePercent", True), ("creditUsagePercent", 101), ("currentPeriod", {})])
def test_billing_missing_or_malformed_is_never_zero(field, value):
    data = json.loads(billing())
    data["config"][field] = value
    with pytest.raises(ValueError):
        grok_usage.parse_billing(json.dumps(data))


def test_http_exception_does_not_persist_credential(tmp_path, monkeypatch, _no_grok_usage_pty_scrape):
    env, path = auth_env(tmp_path)

    class Opener:
        def open(self, request, timeout):
            raise ValueError("Invalid header value: Bearer test-read-only-key")

    monkeypatch.setattr(grok_usage.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(grok_usage, "capture_usage_raw", _no_grok_usage_pty_scrape)
    levels = grok_usage.capture_levels(env=env)
    assert "error" in levels and "test-read-only-key" not in json.dumps(levels)


def test_redirect_cannot_forward_bearer_key(tmp_path, monkeypatch, _no_grok_usage_pty_scrape):
    import http.server
    import threading
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/must-not-receive-key")
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env, path = auth_env(tmp_path)
    monkeypatch.setattr(grok_usage, "BILLING_URL", f"http://127.0.0.1:{server.server_port}/billing")
    monkeypatch.setattr(grok_usage, "capture_usage_raw", _no_grok_usage_pty_scrape)
    try:
        levels = grok_usage.capture_levels(env=env)
        assert "302" in levels["error"] and "quota" not in levels
        assert calls == ["/billing"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
