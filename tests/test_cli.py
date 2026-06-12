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


@pytest.mark.parametrize("command", ["status", "inspect", "docs", "streams", "stream", "eject"])
def test_removed_diagnostic_commands_are_not_public(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


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
