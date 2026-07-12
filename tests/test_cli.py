"""Tests for CLI dispatch."""

import json
import sys
import types

import pytest

from brr.cli import main

from _helpers import init_git_repo


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
    # `brnrd docs` (no topic) lists the bundled topics. Re-introduced as the
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


def test_portal_facets_schema_only_without_run(capsys, monkeypatch):
    # Outside a wake the catalogue still prints — the schema is in code, not in
    # a run — so an operator can always ask "what are the implemented facets?".
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    assert main(["portal", "facets"]) == 0
    out = capsys.readouterr().out
    assert "boundary facet catalogue" in out
    assert "quota [level, required]" in out
    assert "coexisting-runs [state, optional]" in out
    assert "no live run detected" in out


def test_portal_facets_with_live_status(tmp_path, capsys, monkeypatch):
    from brr import facets

    res = facets.build(quota_summary="weekly 42%", branch="brr/x")
    state = tmp_path / "portal-state.json"
    state.write_text(
        json.dumps({"version": 1, "resources": res}) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("BRR_PORTAL_STATE", str(state))
    assert main(["portal", "facets"]) == 0
    out = capsys.readouterr().out
    assert "with live status" in out
    assert "quota [level, required] — known: weekly 42%" in out


def test_portal_facets_json(capsys, monkeypatch):
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    assert main(["portal", "facets", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["key"] for r in rows} == {
        "quota", "spend", "context_window", "coexisting_runs", "remote_scm"
    }


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
            "spend": {"status": "unimplemented", "note": "not metered yet"},
            "context_window": {"status": "unimplemented",
                               "note": "not exposed by this medium"},
            "coexisting_runs": {"status": "unimplemented"},
            "remote_scm": {"status": "absent",
                           "note": "no PR recorded for this branch yet"},
        },
    })
    assert "nothing sent yet" in out
    assert "running long" in out
    assert "spend=unimplemented (not metered yet)" in out
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


def test_bind_accepts_repo_and_gate(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    calls = []

    class Gate:
        @staticmethod
        def bind(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: Gate)

    assert main(["bind", str(repo), "telegram"]) is None

    out = capsys.readouterr().out
    assert calls == [repo / ".brr"]
    assert "project home" in out
    assert "telegram" in out


def test_add_registers_repo_in_connected_account_home(monkeypatch, tmp_path, capsys):
    current = tmp_path / "current"
    target = tmp_path / "target"
    init_git_repo(current)
    init_git_repo(target)
    cloud_dir = current / ".brr" / "gates"
    cloud_dir.mkdir(parents=True)
    (cloud_dir / "cloud.json").write_text(
        json.dumps({
            "brnrd_url": "https://brnrd.example",
            "token": "tok",
            "account_id": "acct-1",
            "repo_id": "repo-1",
        }),
        encoding="utf-8",
    )
    monkeypatch.chdir(current)

    assert main(["add", str(target)]) is None

    out = capsys.readouterr().out
    assert "added target" in out
    # tests/conftest isolates XDG_STATE_HOME at a generated temp path, so read
    # the resolver instead of guessing its parent from this test's tmp_path.
    from brr import account, config as conf

    ctx = account.resolve_context(current, conf.load_config(current), create=False)
    registry = json.loads((ctx.dominion_repo / "account" / "repos.json").read_text())
    assert {item["label"] for item in registry["repos"]} == {"current", "target"}
    assert registry["home_kind"] == "account"
    assert registry["account_id"] == "acct-1"


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
    repo = tmp_path / "repo"
    init_git_repo(repo)
    calls = []

    class FakeGate:
        @staticmethod
        def bind(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)

    main(["bind", str(repo), "telegram"])

    assert calls == [repo / ".brr"]


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


# ── brnrd runners list (step 2, design-runner-cores.md) ───────────────────────


def test_runners_list_text_output(monkeypatch, capsys):
    """Text output shows declared profiles and bundled Core registry."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    # Pretend claude is on PATH, codex and gemini are not
    monkeypatch.setattr(
        runner_cores.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )
    monkeypatch.setattr(
        _shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )

    assert main(["runners", "list"]) == 0
    out = capsys.readouterr().out

    # Declared-profiles section header present
    assert "declared profiles" in out
    # Bundled Core registry section
    assert "bundled Core registry" in out
    # Claude cores appear (Shell is on PATH)
    assert "claude-haiku" in out or "claude-sonnet" in out
    # codex/gemini cores filtered out (Shell not on PATH)
    assert "codex-mini" not in out
    assert "gemini-flash" not in out


def test_runners_list_all_includes_unavailable(monkeypatch, capsys):
    """--all flag includes Cores whose Shell isn't on PATH."""
    import shutil as _shutil

    from brr import runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: None)
    monkeypatch.setattr(_shutil, "which", lambda name: None)

    assert main(["runners", "list", "--all"]) == 0
    out = capsys.readouterr().out
    # With --all, even unavailable Shells appear in the registry section
    assert "claude-haiku" in out or "claude-sonnet" in out


def test_runners_list_json_output(monkeypatch, capsys):
    """--json emits machine-readable JSON with declared + bundled sections."""
    import shutil as _shutil

    from brr import runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")

    assert main(["runners", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "declared" in payload
    assert "bundled_cores" in payload
    assert isinstance(payload["declared"], list)
    assert isinstance(payload["bundled_cores"], list)
    # All bundled cores visible when all Shells are on PATH
    names = [r["name"] for r in payload["bundled_cores"]]
    assert "claude-haiku" in names
    assert "codex-mini" in names


def test_runners_list_marks_current_runner(monkeypatch, capsys, tmp_path):
    """Currently resolved runner is marked with ★ in the text view."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: tmp_path)
    monkeypatch.setattr(runner_mod, "resolve_runner", lambda _root: "claude")
    monkeypatch.setattr(runner_mod, "_load_profiles", lambda _root=None: {
        "claude": {"class": "balanced", "cost_rank": 30},
        "codex": {"class": "balanced", "cost_rank": 25},
    })
    monkeypatch.setattr(
        runner_cores.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")

    assert main(["runners", "list"]) == 0
    out = capsys.readouterr().out
    # The ★ marker should appear next to the currently selected runner
    assert "★" in out
    assert "claude" in out
