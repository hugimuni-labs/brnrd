"""Tests for CLI dispatch."""

import json
import sys
import types

import pytest

from brr.cli import main


def _write_review_pack(path):
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1-test",
                "metadata": {"pr": {"title": "Review pack title"}},
                "reading_order": ["summary:x"],
                "cards": [
                    {
                        "id": "summary:x",
                        "kind": "summary",
                        "identity": {"label": "the change in shape"},
                        "lore": {"descriptive": "a small honest change"},
                        "provenance": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


@pytest.mark.parametrize("command", ["status", "inspect", "streams", "stream", "eject"])
def test_removed_diagnostic_commands_are_not_public(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


def test_docs_lists_topics(capsys):
    # `brr docs` (no topic) lists the bundled topics. Re-introduced as the
    # inspect surface for the portals manual (G5) — the docs module and
    # decision-bundled-docs.md always assumed this command existed.
    assert main(["docs"]) == 0
    out = capsys.readouterr().out
    assert "portals" in out
    assert "execution-map" in out


def test_docs_prints_topic(capsys):
    assert main(["docs", "portals"]) == 0
    out = capsys.readouterr().out
    assert "control-file" in out.lower() or "portal" in out.lower()


def test_docs_unknown_topic_errors(capsys):
    assert main(["docs", "does-not-exist"]) == 1


def test_portal_state_prints_text_view(tmp_path, capsys):
    state = tmp_path / "portal-state.json"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "change_token": "abc123",
                "run": {
                    "id": "run-1",
                    "event_id": "evt-1",
                    "phase": "running",
                    "attempt": 1,
                },
                "attention": {
                    "pending_event_count": 1,
                    "pending_outbox_file_count": 0,
                },
                "inbound": {
                    "events": [
                        {
                            "id": "evt-2",
                            "source": "telegram",
                            "summary": "quick follow-up",
                        }
                    ]
                },
                "outbound": {
                    "replies_current": 1,
                    "replies_other": 0,
                    "outbound_messages": 0,
                    "pending_outbox_files": [],
                },
                "budget": {
                    "elapsed_seconds": 65,
                    "budget_seconds": 3600,
                    "keepalive": {"status": "absent"},
                },
                "card": {"text": "working"},
            }
        ),
        encoding="utf-8",
    )

    assert main(["portal", "state", "--path", str(state)]) == 0

    out = capsys.readouterr().out
    assert "run=run-1" in out
    assert "token=abc123" in out
    assert "evt-2 telegram: quick follow-up" in out
    assert "card: working" in out


def test_portal_state_prints_json_from_env(tmp_path, capsys, monkeypatch):
    state = tmp_path / "portal-state.json"
    state.write_text('{"version": 1, "run": {"id": "run-env"}}\n', encoding="utf-8")
    monkeypatch.setenv("BRR_PORTAL_STATE", str(state))

    assert main(["portal", "state", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["id"] == "run-env"


def test_format_portal_state_surfaces_missing_data():
    from brr.cli import _format_portal_state

    out = _format_portal_state({
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "outbound": {"replies_current": 0, "replies_other": 0,
                     "outbound_messages": 0, "any_sent": False},
        "budget": {"elapsed_seconds": 4000, "budget_seconds": 3600,
                   "long_running": True, "keepalive": {"status": "-"}},
        "resources": {
            "quota": {"status": "absent", "note": "no snapshot for this medium"},
            "cost": {"status": "unimplemented", "note": "not metered yet"},
            "coexisting_runs": {"status": "unimplemented"},
            "remote_scm": {"status": "absent",
                           "note": "no PR recorded for this branch yet"},
        },
    })
    assert "nothing sent yet" in out
    assert "running long" in out
    assert "cost=unimplemented (not metered yet)" in out
    assert "remote-scm=absent (no PR recorded for this branch yet)" in out
    assert "unavailable" not in out


def test_portal_state_errors_without_file(capsys, monkeypatch):
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: None)

    assert main(["portal", "state"]) == 1
    assert "no live portal-state.json" in capsys.readouterr().err


def test_run_requires_instruction():
    with pytest.raises(SystemExit):
        main(["run"])


def test_review_prints_pr_title_and_body(tmp_path, capsys):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)

    assert main(["review", str(pack), "--pr-title", "--fallback-title", "fallback"]) == 0
    assert "Review pack title" in capsys.readouterr().out

    assert main(["review", str(pack), "--pr-body", "--render-url", "https://r.example"]) == 0
    body = capsys.readouterr().out
    assert "## Summary" in body
    assert "https://r.example" in body
    assert "diffense:pack:v1" in body


def test_review_relay_prefers_gist_owned_pack(tmp_path, capsys, monkeypatch):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)

    from brr.diffense import gist

    monkeypatch.setattr(
        gist,
        "create_pack_gist",
        lambda _pack, **_kwargs: gist.GistPack(
            html_url="https://gist.github.com/octo/abc",
            raw_url="https://gist.githubusercontent.com/octo/abc/raw/sha/diffense-pack.json",
        ),
    )
    monkeypatch.setattr(gist, "renderer_shell_available", lambda _base_url: True)
    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)

    assert main(["review", str(pack), "--pr-body", "--relay"]) == 0

    body = capsys.readouterr().out
    assert "https://brnrd.dev/r?pack=" in body
    assert "Pack source: https://gist.github.com/octo/abc" in body


def test_review_relay_falls_back_to_transient_cloud_relay(
    tmp_path, capsys, monkeypatch,
):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)
    brr_dir = tmp_path / ".brr"

    from brr.diffense import gist
    import brr.gates as gates

    cloud = types.ModuleType("brr.gates.cloud")
    cloud.is_configured = lambda _brr_dir: True
    cloud.relay_pack = lambda _brr_dir, _pack: "https://brnrd.example/r/tok"
    monkeypatch.setattr(gist, "renderer_shell_available", lambda _base_url: True)
    monkeypatch.setattr(gist, "review_url_available", lambda _url: True)
    monkeypatch.setattr(gist, "create_pack_gist", lambda _pack, **_kwargs: None)
    monkeypatch.setattr("brr.cli._diffense_current_repo", lambda: None)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: brr_dir)
    monkeypatch.setitem(sys.modules, "brr.gates.cloud", cloud)
    monkeypatch.setattr(gates, "cloud", cloud, raising=False)

    assert main(["review", str(pack), "--pr-body", "--relay"]) == 0

    body = capsys.readouterr().out
    assert "https://brnrd.example/r/tok" in body
    assert "Transient link" in body


def test_review_relay_falls_back_when_renderer_shell_is_not_live(
    tmp_path, capsys, monkeypatch,
):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)
    brr_dir = tmp_path / ".brr"

    from brr.diffense import gist
    import brr.gates as gates

    cloud = types.ModuleType("brr.gates.cloud")
    cloud.is_configured = lambda _brr_dir: True
    cloud.relay_pack = lambda _brr_dir, _pack: "https://brnrd.example/r/tok"
    monkeypatch.setattr(gist, "renderer_shell_available", lambda _base_url: False)
    monkeypatch.setattr(gist, "review_url_available", lambda _url: True)

    def fail_create(*_args, **_kwargs):
        raise AssertionError("dead renderer links should not create gists")

    monkeypatch.setattr(gist, "create_pack_gist", fail_create)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: brr_dir)
    monkeypatch.setitem(sys.modules, "brr.gates.cloud", cloud)
    monkeypatch.setattr(gates, "cloud", cloud, raising=False)

    assert main(["review", str(pack), "--pr-body", "--relay"]) == 0

    body = capsys.readouterr().out
    assert "https://brnrd.example/r/tok" in body
    assert "Pack source" not in body
    assert "Transient link" in body


def test_review_relay_omits_link_when_transient_relay_render_fails(
    tmp_path, capsys, monkeypatch,
):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)
    brr_dir = tmp_path / ".brr"

    from brr.diffense import gist
    import brr.gates as gates

    cloud = types.ModuleType("brr.gates.cloud")
    cloud.is_configured = lambda _brr_dir: True
    cloud.relay_pack = lambda _brr_dir, _pack: "https://brnrd.example/r/tok"
    monkeypatch.setattr(gist, "renderer_shell_available", lambda _base_url: False)
    monkeypatch.setattr(gist, "review_url_available", lambda _url: False)
    monkeypatch.setattr(gist, "create_pack_gist", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: brr_dir)
    monkeypatch.setitem(sys.modules, "brr.gates.cloud", cloud)
    monkeypatch.setattr(gates, "cloud", cloud, raising=False)

    assert main(["review", str(pack), "--pr-body", "--relay"]) == 0

    body = capsys.readouterr().out
    assert "Interactive review" not in body
    assert "https://brnrd.example/r/tok" not in body
    assert "## Summary" in body


def test_up_dev_reload_flag_passes_to_daemon(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "brr.daemon.start",
        lambda repo_root, *, dev_reload=None: calls.append(
            (repo_root, dev_reload),
        ),
    )

    main(["up", "--dev-reload"])

    assert calls == [(tmp_path, True)]


def test_daemon_up_foreground_uses_existing_daemon_start(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "brr.daemon.start",
        lambda repo_root, *, dev_reload=None: calls.append(
            (repo_root, dev_reload),
        ),
    )

    main(["daemon", "up", "--foreground", "--dev-reload"])

    assert calls == [(tmp_path, True)]


def test_daemon_install_dispatches_to_native_installer(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "brr.daemon_install.install",
        lambda **kwargs: calls.append(kwargs),
    )

    main(["daemon", "install", "--no-start", "--no-linger"])

    assert calls == [
        {
            "no_start": True,
            "prompt_linger": False,
            "assume_yes_linger": False,
        },
    ]


def test_daemon_logs_dispatches_to_native_helper(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "brr.daemon_install.logs",
        lambda **kwargs: calls.append(kwargs) or 0,
    )

    assert main(["daemon", "logs", "-n", "25", "--no-follow"]) == 0
    assert calls == [{"follow": False, "lines": 25}]


def test_daemon_status_does_not_require_repo(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: tmp_path / ".brr")
    monkeypatch.setattr(
        "brr.daemon_install.status",
        lambda *, direct_brr_dir=None: calls.append(direct_brr_dir),
    )

    main(["daemon", "status"])

    assert calls == [tmp_path / ".brr"]


def test_agent_inject_prints_wake_context(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_inject(repo_root, *, task_text=None):
        seen["args"] = (repo_root, task_text)
        return "WAKE-CONTEXT-DIGEST"

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: tmp_path)
    monkeypatch.setattr("brr.prompts.build_injected_context", fake_inject)

    assert main(["agent", "inject", "--task", "fix the parser"]) == 0
    assert seen["args"] == (tmp_path, "fix the parser")
    assert "WAKE-CONTEXT-DIGEST" in capsys.readouterr().out


def test_agent_inject_requires_repo(monkeypatch):
    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    assert main(["agent", "inject"]) == 2


def test_agent_inject_reports_empty_dominion(monkeypatch, tmp_path):
    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "brr.prompts.build_injected_context",
        lambda repo_root, *, task_text=None: "",
    )
    assert main(["agent", "inject"]) == 1


def test_agent_requires_subcommand(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["agent"])
    assert exc.value.code == 2


def test_bind_dispatches_to_gate_bind(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def bind(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["bind", "telegram"])

    assert calls == [tmp_path / ".brr"]


def test_setup_dispatches_to_gate_setup(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def setup(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["setup", "telegram"])

    assert calls == [tmp_path / ".brr"]


def test_setup_falls_back_to_auth_then_bind(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def auth(brr_dir):
            calls.append(("auth", brr_dir))

        @staticmethod
        def bind(brr_dir):
            calls.append(("bind", brr_dir))

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["setup", "telegram"])

    assert calls == [
        ("auth", tmp_path / ".brr"),
        ("bind", tmp_path / ".brr"),
    ]
