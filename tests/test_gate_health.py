"""Per-ingestion-gate liveness recording and local status rendering (#360)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from brr.daemon_install import _print_gate_health
from brr.gates import runtime


class _StopLoop(BaseException):
    pass


def test_gate_health_model_and_migration_column_exist():
    from brnrd import migrations
    from brnrd.models import Daemon

    assert "gate_health_json" in Daemon.__table__.c
    statements: list[str] = []

    class FakeConn:
        def execute(self, statement):
            statements.append(str(statement))

    migrations._migrate_daemons(FakeConn())

    assert any("gate_health_json" in statement for statement in statements)


def test_run_loop_writes_health_after_success(tmp_path):
    brr_dir = tmp_path / ".brr"
    calls = 0

    def loop_once():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise _StopLoop

    with pytest.raises(_StopLoop):
        runtime.run_loop(
            loop_once,
            label="test",
            brr_dir=brr_dir,
            gate="telegram",
        )

    health = runtime.load_health(brr_dir, "telegram")
    assert health["last_poll_ok"] is not None
    assert health["last_error"] is None
    assert not runtime.health_path(brr_dir, "telegram").with_suffix(
        ".json.tmp"
    ).exists()


def test_run_loop_error_preserves_last_success(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    runtime.record_health(brr_dir, "slack", ok=True)
    last_poll_ok = runtime.load_health(brr_dir, "slack")["last_poll_ok"]
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: (_ for _ in ()).throw(_StopLoop()))

    with pytest.raises(_StopLoop):
        runtime.run_loop(
            lambda: (_ for _ in ()).throw(RuntimeError("token expired")),
            label="slack",
            brr_dir=brr_dir,
            gate="slack",
        )

    health = runtime.load_health(brr_dir, "slack")
    assert health["last_poll_ok"] == last_poll_ok
    assert health["last_error"] == "token expired"
    assert health["last_error_at"] is not None


def test_gate_health_classifies_never_and_degraded_boundary(tmp_path):
    brr_dir = tmp_path / ".brr"
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    gates_dir = brr_dir / "gates"
    gates_dir.mkdir(parents=True)
    (gates_dir / "telegram.health.json").write_text(
        json.dumps({"last_poll_ok": (now - timedelta(seconds=300)).isoformat()}),
        encoding="utf-8",
    )
    (gates_dir / "slack.health.json").write_text(
        json.dumps({"last_poll_ok": (now - timedelta(seconds=301)).isoformat()}),
        encoding="utf-8",
    )

    rows = runtime.gate_health_rows(
        brr_dir,
        gates=["telegram", "slack", "github"],
        now=now,
    )

    assert [(row["gate"], row["age_seconds"], row["status"]) for row in rows] == [
        ("telegram", 300, "ok"),
        ("slack", 301, "degraded"),
        ("github", None, "never"),
    ]


def test_gate_health_hides_error_after_a_newer_success(tmp_path):
    brr_dir = tmp_path / ".brr"
    now = datetime(2026, 7, 18, 20, 0, tzinfo=timezone.utc)
    path = runtime.health_path(brr_dir, "telegram")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "last_poll_ok": now.isoformat(),
            "last_error": "network timed out",
            "last_error_at": (now - timedelta(seconds=10)).isoformat(),
        }),
        encoding="utf-8",
    )

    [row] = runtime.gate_health_rows(brr_dir, gates=["telegram"], now=now)

    assert row["status"] == "ok"
    assert row["last_error"] is None


def test_gate_health_keeps_error_newer_than_last_success(tmp_path):
    brr_dir = tmp_path / ".brr"
    now = datetime(2026, 7, 18, 20, 0, tzinfo=timezone.utc)
    path = runtime.health_path(brr_dir, "cloud")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "last_poll_ok": (now - timedelta(seconds=10)).isoformat(),
            "last_error": "upstream 502",
            "last_error_at": now.isoformat(),
        }),
        encoding="utf-8",
    )

    [row] = runtime.gate_health_rows(brr_dir, gates=["cloud"], now=now)

    assert row["status"] == "degraded"
    assert row["last_error"] == "upstream 502"


def test_server_fingerprint_round_trips_and_stamps_fetched_at(tmp_path):
    brr_dir = tmp_path / ".brr"
    assert runtime.load_server_fingerprint(brr_dir, "cloud") is None

    # Stored verbatim: the daemon is a courier for whatever the server sent,
    # so the payload is the server's current shape and not a copy of it kept
    # in sync here.
    server = {
        "build": {"commit": "bebd5c1d", "built_at": "2026-07-30T10:19:01+00:00", "started_at": "2026-07-30T10:28:49+00:00"},
        "github": {"bot_login": "brnrd-bot", "app_slug": "brnrd-dev", "trigger_label": "brnrd", "trigger_aliases": ["brnrd", "brr"], "webhook_secret_set": True, "bot_token_set": True},
    }
    runtime.save_server_fingerprint(brr_dir, "cloud", server)

    loaded = runtime.load_server_fingerprint(brr_dir, "cloud")
    assert loaded["build"]["commit"] == "bebd5c1d"
    assert loaded["github"]["bot_login"] == "brnrd-bot"
    assert loaded["fetched_at"]  # local stamp added at write time
    assert not runtime.server_fingerprint_path(brr_dir, "cloud").with_suffix(
        ".json.tmp"
    ).exists()


def test_server_fingerprint_corrupt_file_reads_as_absent(tmp_path):
    brr_dir = tmp_path / ".brr"
    path = runtime.server_fingerprint_path(brr_dir, "cloud")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert runtime.load_server_fingerprint(brr_dir, "cloud") is None


#: Stands in for "a poll that just succeeded" inside the parametrize table
#: below, and is replaced with a real timestamp *when the test runs*.
#:
#: It has to be a marker rather than a literal, because a `parametrize`
#: argument is evaluated once at **module import** — collection time — while
#: the assertion it feeds is about *freshness* against
#: `GATE_HEALTH_DEGRADED_AFTER_S` (300 s), measured at call time. The gap
#: between those two moments is however long the suite takes to get here, so
#: a literal `datetime.now()` in the table is a timebomb armed by suite
#: runtime: green in isolation, green in a fast CI run, and red the first
#: time the backend leg needs more than five minutes to reach this file.
#:
#: It went off on 2026-08-03 — a 535 s backend leg reached this test at
#: 354 s and read `telegram: degraded; last successful poll 354s ago` where
#: the case says `telegram: ok`. A fixture picks a moment; this one picked
#: the wrong one, and nothing about the failure named the clock.
_NOW = "<now>"


def _with_current_timestamps(health: dict) -> dict:
    """Replace every :data:`_NOW` marker with the time *right now*."""
    return {
        gate: {
            key: (datetime.now(timezone.utc).isoformat() if value is _NOW else value)
            for key, value in payload.items()
        }
        for gate, payload in health.items()
    }


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        ({}, ["telegram: never", "slack: never"]),
        (
            {"telegram": {"last_poll_ok": _NOW}},
            ["telegram: ok", "slack: never"],
        ),
        (
            {
                "telegram": {"last_poll_ok": _NOW},
                "slack": {
                    "last_poll_ok": None,
                    "last_error": "bad auth",
                    "last_error_at": _NOW,
                },
            },
            ["telegram: ok", "slack: never", "last error: bad auth"],
        ),
    ],
    ids=["zero-health-files", "partial-health-files", "complete-health-files"],
)
def test_local_status_renders_configured_gate_health(
    tmp_path, monkeypatch, capsys, health, expected
):
    brr_dir = tmp_path / ".brr"
    monkeypatch.setattr(runtime, "configured_gates", lambda _brr: ["telegram", "slack"])
    for gate, payload in _with_current_timestamps(health).items():
        path = runtime.health_path(brr_dir, gate)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    _print_gate_health(brr_dir)

    output = capsys.readouterr().out
    for text in expected:
        assert text in output


def test_the_health_table_never_freezes_a_clock_reading_at_collection_time():
    """The guard for the class, not for the instance.

    Re-introducing a literal ``datetime.now(...)`` into the parametrize table
    above is invisible in review and green in isolation; it only fails once
    the suite is slow enough to age past ``GATE_HEALTH_DEGRADED_AFTER_S``,
    which is a property of *the rest of the suite*, not of this file. So the
    thing to assert is the shape: a case that means "just polled" says
    :data:`_NOW`, and the clock is read when the test runs.

    Reads the marks off the test function rather than re-declaring the table,
    so it cannot drift out of step with what actually runs.
    """
    cases = [
        mark.args[1]
        for mark in test_local_status_renders_configured_gate_health.pytestmark
        if mark.name == "parametrize"
    ][0]
    for health, _expected in cases:
        for payload in health.values():
            for key, value in payload.items():
                if not isinstance(value, str) or value is _NOW:
                    continue
                try:
                    datetime.fromisoformat(value)
                except ValueError:
                    continue  # a plain string like an error message — fine
                raise AssertionError(
                    f"{key}={value!r} freezes a clock reading at collection "
                    f"time; use the _NOW marker instead"
                )

    stamped = _with_current_timestamps({"telegram": {"last_poll_ok": _NOW}})
    age = (
        datetime.now(timezone.utc)
        - datetime.fromisoformat(stamped["telegram"]["last_poll_ok"])
    ).total_seconds()
    assert 0 <= age < 5
