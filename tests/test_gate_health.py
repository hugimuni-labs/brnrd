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


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        ({}, ["telegram: never", "slack: never"]),
        (
            {"telegram": {"last_poll_ok": datetime.now(timezone.utc).isoformat()}},
            ["telegram: ok", "slack: never"],
        ),
        (
            {
                "telegram": {"last_poll_ok": datetime.now(timezone.utc).isoformat()},
                "slack": {
                    "last_poll_ok": None,
                    "last_error": "bad auth",
                    "last_error_at": datetime.now(timezone.utc).isoformat(),
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
    for gate, payload in health.items():
        path = runtime.health_path(brr_dir, gate)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    _print_gate_health(brr_dir)

    output = capsys.readouterr().out
    for text in expected:
        assert text in output
