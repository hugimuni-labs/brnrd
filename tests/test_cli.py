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

    assert main(["gate", "bind", str(repo), "telegram"]) is None

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

    assert main(["account", "add", str(target)]) is None

    out = capsys.readouterr().out
    assert "added target" in out
    # tests/conftest isolates XDG_STATE_HOME at a generated temp path, so read
    # the resolver instead of guessing its parent from this test's tmp_path.
    from brr import account, config as conf

    ctx = account.resolve_context(current, conf.load_config(current), create=False)
    registry = json.loads((ctx.dominion_repo / "account" / "repos.json").read_text())
    assert {item["label"] for item in registry["repos"]} == {
        "home", "current", "target",
    }
    assert registry["home_kind"] == "account"
    assert registry["account_id"] == "acct-1"


def test_home_link_yes_asks_nothing(monkeypatch, tmp_path, capsys):
    """``--yes`` is the whole non-interactive contract: no confirm, no stdin read."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    from brr import home_link

    calls = []

    def fake_link_home(repo_root, cfg, **kwargs):
        calls.append(kwargs)
        results = [
            home_link.RepoLinkResult("dominion", repo, "https://x/d", "created", True),
            home_link.RepoLinkResult("knowledge", repo, "https://x/k", "created", True),
        ]
        on_result = kwargs.get("on_result")
        if on_result is not None:
            for result in results:
                on_result(result)
        return results

    monkeypatch.setattr(home_link, "link_home", fake_link_home)

    def _fail_confirm(*_a, **_kw):  # pragma: no cover
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr("brr.adopt._confirm", _fail_confirm)

    assert main(["home", "link", "--yes"]) is None

    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "dominion: created" in out
    assert "knowledge: created" in out


def test_home_link_without_yes_needs_a_tty(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit) as exc:
        main(["home", "link"])
    assert "--yes" in str(exc.value)


def test_home_link_reports_actionable_error_with_no_traceback(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    from brr import home_link

    def boom(*a, **kw):
        raise home_link.HomeLinkError("gh is not authenticated — run `gh auth login` first")

    monkeypatch.setattr(home_link, "link_home", boom)

    with pytest.raises(SystemExit) as exc:
        main(["home", "link", "--yes"])
    assert "gh is not authenticated" in str(exc.value)


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

    main(["gate", "bind", str(repo), "telegram"])

    assert calls == [repo / ".brr"]


def test_setup_dispatches_to_gate_setup(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def setup(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["gate", "setup", "telegram"])

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

    main(["gate", "setup", "telegram"])

    assert calls == [
        ("auth", tmp_path / ".brr"),
        ("bind", tmp_path / ".brr"),
    ]


# ── brnrd runners list (step 2, design-runner-cores.md) ───────────────────────


def test_runners_list_text_output(monkeypatch, capsys):
    """Text output shows unified catalog with availability and stale marks."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    # Pretend claude is on PATH, codex is not
    monkeypatch.setattr(
        runner_cores.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )
    monkeypatch.setattr(
        _shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )
    monkeypatch.setattr(
        runner_mod.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )

    assert main(["runners", "list"]) == 0
    out = capsys.readouterr().out

    # Unified catalog header
    assert "runner catalog" in out
    # Claude cores appear (available ✓)
    assert "claude-haiku" in out or "claude-sonnet" in out
    # Unavailable profiles also shown (with ✗)
    assert "codex-mini" in out
    assert "✗" in out


def test_runners_list_all_is_noop(monkeypatch, capsys):
    """--all is accepted for backwards-compat; unavailable rows appear by default."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: None)
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name: None)

    assert main(["runners", "list", "--all"]) == 0
    out = capsys.readouterr().out
    # Even with no shells on PATH, profiles still shown with ✗ marks
    assert "claude-haiku" in out or "claude-sonnet" in out


def test_runners_list_json_output(monkeypatch, capsys):
    """--json emits machine-readable JSON with unified profiles list."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_mod.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    assert main(["runners", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "profiles" in payload
    assert isinstance(payload["profiles"], list)
    # All bundled cores visible when all Shells are on PATH
    names = [r["name"] for r in payload["profiles"]]
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
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert main(["runners", "list"]) == 0
    out = capsys.readouterr().out
    # The ★ marker should appear next to the currently selected runner
    assert "★" in out
    assert "claude" in out


# ── the CLI surface itself (#49) ──────────────────────────────────────────────
#
# The verb list is a contract: `brnrd --help` is the front door, and every doc,
# prompt, and muscle-memory habit spells against it. Before #49 the tree had
# drifted for months without a single test noticing. These pin the surface so
# the next drift is a test failure, not a discovery.


def _subparsers_action():
    import argparse

    from brr.cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("brnrd has no subparsers")


def test_public_commands_are_exactly_what_help_lists():
    from brr.cli import PUBLIC_COMMANDS

    listed = [c.dest for c in _subparsers_action()._choices_actions]
    assert sorted(listed) == sorted(PUBLIC_COMMANDS)


def test_all_commands_are_exactly_what_parses():
    from brr.cli import ALL_COMMANDS

    assert sorted(_subparsers_action().choices) == sorted(ALL_COMMANDS)


def test_help_stays_small_enough_to_read():
    # The whole point of the noun consolidation: a front door a human can scan.
    # Not a golden count — a ceiling. Adding a top-level verb should require
    # arguing that it earns one of these slots.
    from brr.cli import PUBLIC_COMMANDS

    assert len(PUBLIC_COMMANDS) <= 18


def test_hidden_commands_parse_but_are_not_listed():
    from brr.cli import HIDDEN_COMMANDS

    action = _subparsers_action()
    listed = {c.dest for c in action._choices_actions}
    for name in HIDDEN_COMMANDS:
        assert name in action.choices, f"{name} must still parse"
        assert name not in listed, f"{name} must not spend a --help line"


def test_help_does_not_leak_the_suppress_sentinel(capsys):
    # `help=argparse.SUPPRESS` on add_parser does not hide a subparser — it
    # renders a literal "==SUPPRESS==" line. Omitting the kwarg is the lever.
    # This pins the symptom so nobody "fixes" the hiding back into a leak.
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "SUPPRESS" not in out


@pytest.mark.parametrize(
    "argv,pointer",
    [
        (["auth", "telegram"], "brnrd gate auth"),
        (["bind", ".", "telegram"], "brnrd gate bind"),
        (["setup", "telegram"], "brnrd gate setup"),
        (["add", "."], "brnrd account add"),
        (["connect"], "brnrd account connect"),
    ],
)
def test_retired_spellings_fail_with_a_pointer(argv, pointer, capsys):
    # Pre-release: the old spellings do not survive as silent aliases. They
    # fail — but they fail *pointing*, not with argparse's bare "invalid
    # choice", which would leave the reader to guess where the verb went.
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert pointer in capsys.readouterr().err


def test_retired_spellings_do_not_run_their_old_command(monkeypatch):
    # The pointer must fire *before* any gate work: a retired spelling that
    # still authed would be an alias wearing an error message.
    calls = []
    monkeypatch.setattr("brr.cli._load_gate", lambda name: calls.append(name))
    for argv in (["auth", "telegram"], ["setup", "telegram"], ["bind", ".", "telegram"]):
        with pytest.raises(SystemExit):
            main(argv)
    assert calls == []


def test_up_and_daemon_up_are_the_same_implementation():
    # The #49 drift in one assertion: `up` used to be a second implementation
    # that skipped the installed service.
    from brr.cli import cmd_daemon_up

    action = _subparsers_action()
    top_up = action.choices["up"].get_default("func")
    daemon_up = _subcommand_default(action.choices["daemon"], "up", "func")
    assert top_up is daemon_up is cmd_daemon_up


def test_daemon_up_with_dev_reload_never_delegates_to_the_service(monkeypatch):
    # `--dev-reload` is a foreground concept the installed service cannot
    # carry; delegating would silently drop it (and lie "service started"
    # while the caller's flag did nothing).
    import argparse
    from pathlib import Path

    from brr.cli import cmd_daemon_up

    delegated = []
    monkeypatch.setattr(
        "brr.daemon_install.start_service", lambda: delegated.append(True) or 0,
    )
    started = []
    monkeypatch.setattr("brr.daemon.start", lambda root, dev_reload: started.append(dev_reload))
    monkeypatch.setattr("brr.cli._repo_root", lambda: Path("/tmp/repo"))

    args = argparse.Namespace(foreground=False, dev_reload=True)
    cmd_daemon_up(args)

    assert delegated == []
    assert started == [True]


def test_daemon_up_service_path_reports_available_update(monkeypatch, capsys):
    import argparse

    from brr import release_availability
    from brr.cli import cmd_daemon_up

    monkeypatch.setattr("brr.daemon_install.start_service", lambda: 0)
    monkeypatch.setattr(
        release_availability,
        "refresh_if_stale",
        lambda _root: release_availability.Availability("0.1.0", "0.2.0"),
    )

    assert cmd_daemon_up(argparse.Namespace(foreground=False, dev_reload=None)) == 0
    assert "[brnrd] update available: 0.1.0 → 0.2.0" in capsys.readouterr().out


def test_down_and_daemon_down_are_the_same_implementation():
    from brr.cli import cmd_daemon_down

    action = _subparsers_action()
    top_down = action.choices["down"].get_default("func")
    daemon_down = _subcommand_default(action.choices["daemon"], "down", "func")
    assert top_down is daemon_down is cmd_daemon_down


def test_top_level_up_accepts_the_same_flags_as_daemon_up():
    # A thin alias that dropped --foreground would be a different verb.
    action = _subparsers_action()
    top = {o for a in action.choices["up"]._actions for o in a.option_strings}
    nested_parser = _subcommand_parser(action.choices["daemon"], "up")
    nested = {o for a in nested_parser._actions for o in a.option_strings}
    assert top == nested


def _subcommand_parser(parser, name):
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            return action.choices[name]
    raise AssertionError(f"no subcommand {name}")


def _subcommand_default(parser, name, key):
    return _subcommand_parser(parser, name).get_default(key)


# ── brnrd gate list / account status / completions (#49, new surfaces) ────────


def test_gate_list_reads_each_gates_own_is_configured(monkeypatch, tmp_path, capsys):
    seen = []

    def fake_load(name):
        seen.append(name)
        mod = types.SimpleNamespace(is_configured=lambda brr_dir: name == "telegram")
        return mod

    monkeypatch.setattr("brr.cli._load_gate", fake_load)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: tmp_path / ".brr")

    assert main(["gate", "list"]) == 0

    from brr.cli import GATES

    assert seen == list(GATES)
    out = capsys.readouterr().out
    assert "✓ telegram   configured" in out
    assert "· slack      not configured" in out


def test_gate_list_json_shape(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "brr.cli._load_gate",
        lambda name: types.SimpleNamespace(is_configured=lambda d: False),
    )
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: tmp_path / ".brr")

    assert main(["gate", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    from brr.cli import GATES

    assert [g["name"] for g in payload["gates"]] == list(GATES)
    assert all(g["configured"] is False for g in payload["gates"])


def test_gate_list_outside_a_repo_reports_unknown_not_false(monkeypatch, capsys):
    # Honesty: with no .brr to read, "not configured" would be a claim we
    # cannot support. The catalogue still prints; each gate reports unknown.
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: None)

    assert main(["gate", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["brr_dir"] is None
    assert all(g["configured"] is None for g in payload["gates"])


def test_gate_list_survives_a_broken_gate(monkeypatch, tmp_path, capsys):
    def boom(name):
        if name == "slack":
            raise RuntimeError("gate state is corrupt")
        return types.SimpleNamespace(is_configured=lambda d: True)

    monkeypatch.setattr("brr.cli._load_gate", boom)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: tmp_path / ".brr")

    assert main(["gate", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    states = {g["name"]: g["configured"] for g in payload["gates"]}
    assert states["slack"] is None  # unknown, not a crash
    assert states["telegram"] is True


def test_account_status_does_not_create_the_home_it_reports(monkeypatch, tmp_path):
    # A status command that materializes a home is lying about what it found.
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    seen = {}

    real = __import__("brr.account", fromlist=["resolve_context"]).resolve_context

    def spy(repo_root, cfg=None, *, create=True):
        seen["create"] = create
        return real(repo_root, cfg, create=create)

    monkeypatch.setattr("brr.account.resolve_context", spy)

    assert main(["account", "status"]) == 0
    assert seen["create"] is False


def test_account_status_json_reports_the_resolved_home(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    assert main(["account", "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] in {"project", "account"}
    assert payload["dominion_repo"]
    assert any(r["default"] for r in payload["repos"])
    home = next(r for r in payload["repos"] if r["label"] == "home")
    assert home["kind"] == "home"
    assert home["root"] == payload["dominion_repo"]


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completions_emit_every_public_verb(shell, capsys):
    from brr.cli import PUBLIC_COMMANDS

    assert main(["completions", shell]) == 0
    out = capsys.readouterr().out
    for verb in PUBLIC_COMMANDS:
        assert verb in out, f"{shell} completions omit {verb}"


def test_completions_track_the_parser_not_a_hand_list(capsys):
    # The generator walks the live tree, so a new subcommand shows up in
    # completions without anyone remembering to update a table.
    assert main(["completions", "bash"]) == 0
    out = capsys.readouterr().out
    assert "add connect relabel status" in out  # brnrd account
    assert "auth bind list setup" in out  # brnrd gate


def test_completions_omit_retired_and_hidden_spellings(capsys):
    # Scoped to the *top-level* completion context on purpose: `add`, `connect`,
    # `auth`, `bind`, `setup` are retired as top-level verbs but are the real
    # spellings one level down (`brnrd account add`), so a bare substring check
    # would fail on the very nesting this slice introduced.
    from brr.cli import HIDDEN_COMMANDS, RETIRED_COMMANDS

    assert main(["completions", "fish"]) == 0
    top_level = {
        line.split('-a "')[1].rstrip('"')
        for line in capsys.readouterr().out.splitlines()
        if "__fish_use_subcommand" in line
    }
    assert top_level.isdisjoint(RETIRED_COMMANDS)
    assert top_level.isdisjoint(HIDDEN_COMMANDS)


def test_completions_rejects_an_unknown_shell():
    with pytest.raises(SystemExit) as exc:
        main(["completions", "nushell"])
    assert exc.value.code == 2


# ── brnrd kb — optional query (#649) ─────────────────────────────────────────


def _kb_repo(tmp_path):
    """Set up a minimal repo+kb dir for cmd_kb tests."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    kb = repo / "kb"
    kb.mkdir()
    # Two pages so compute_graph_stats returns a non-empty GraphStats.
    (kb / "index.md").write_text("# Index\n", encoding="utf-8")
    (kb / "subject-test.md").write_text("# Test subject\n", encoding="utf-8")
    return repo, kb


def test_cmd_kb_no_query_exits_0_and_prints_graph_header(tmp_path, capsys, monkeypatch):
    """brnrd kb with no query prints the graph report and exits 0."""
    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None: kb)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: kb)

    rc = main(["kb"])
    out = capsys.readouterr().out
    assert rc == 0
    # Assert on a real section header from format_graph_stats, not a hardcoded guess.
    from brr.kb_health import format_graph_stats, GraphStats
    sample = format_graph_stats(GraphStats(total_pages=1, total_bytes=1))
    header = sample.splitlines()[0]
    assert header in out


def test_cmd_kb_no_query_on_unresolvable_kb_is_not_a_silent_success(
    tmp_path, capsys, monkeypatch
):
    """A root with no resolvable kb must say so and exit non-zero.

    `knowledge.active_kb_dir` returns None whenever `sources()` finds neither
    `home` nor `repo-kb` — a fresh checkout before `brnrd init`, and, far more
    commonly, **any run worktree**, which is the default `worktree` environment
    every brnrd run uses. Driven against a live worktree, that path printed
    zero bytes to stdout, zero to stderr, and exited 0: a silent success, and
    strictly worse than the `exit 2` usage error this command replaced.

    Note the sibling test above monkeypatches `active_kb_dir` to a real
    directory. That fixture chooses a resolution the runtime does not produce
    from a worktree — the same shape as a fixture that chooses a lifecycle
    moment. This test asserts the resolution the runtime *does* produce there.
    """
    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None: kb)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: None)

    rc = main(["kb"])
    captured = capsys.readouterr()
    assert rc == 1, "an unresolvable kb must not report success"
    assert captured.out.strip(), "an unresolvable kb must not print nothing"
    # Name what was looked for and where, so the reader can act on it.
    assert "[brnrd kb]" in captured.out
    assert str(repo) in captured.out
    # The fixture repo *does* have a populated `kb/`. Reporting on it here
    # would mean None had been read as "unspecified" rather than as "none".
    assert "Graph stats" not in captured.out


def test_cmd_kb_no_query_on_empty_kb_dir_is_not_a_silent_success(
    tmp_path, capsys, monkeypatch
):
    """A resolved-but-pageless kb dir must say so and exit non-zero.

    `format_graph_stats` renders zeroed stats as the empty string, so the
    unguarded path printed zero bytes and returned 0 — a silent success, and
    strictly worse than the `exit 2` usage error this command replaced.
    """
    repo, _kb = _kb_repo(tmp_path)
    empty = tmp_path / "empty-kb"
    empty.mkdir()
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None: empty)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: empty)

    rc = main(["kb"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no pages" in captured.out
    assert str(empty) in captured.out


def test_cmd_kb_no_query_names_the_directory_it_walked(
    tmp_path, capsys, monkeypatch
):
    """The report says which knowledge root it walked.

    One repo has several plausible knowledge roots — an account-scoped home, a
    project-scoped home, the `.brnrd-kb` checkout clone, a committed `kb/` —
    and which one `active_kb_dir` returns depends on where the command ran.
    Driven 2026-07-24: from one worktree it resolved to a project-scoped root
    holding a single page while the same wake's kb-health block reported 155.
    Both numbers were stated flatly and neither named its corpus. A report a
    reader cannot attribute is a report they cannot reconcile.
    """
    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None: kb)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: kb)

    rc = main(["kb"])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(kb) in out, "the report must name the directory it walked"


def test_cmd_kb_with_query_hit_exits_0(tmp_path, capsys, monkeypatch):
    """brnrd kb <query> with a match exits 0 and prints the hit — unchanged."""
    repo, kb = _kb_repo(tmp_path)
    (kb / "needle-page.md").write_text("the needle is here\n", encoding="utf-8")
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None: kb)

    rc = main(["kb", "needle"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "needle" in out


def test_cmd_kb_with_query_no_match_exits_1(tmp_path, capsys, monkeypatch):
    """brnrd kb <query> with no match exits 1 — unchanged behaviour."""
    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None: kb)

    rc = main(["kb", "xyzzy-no-such-term-8675309"])
    assert rc == 1
