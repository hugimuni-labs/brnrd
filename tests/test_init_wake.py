"""Tests for the init wake — #507 Layer 3.

Three seams are pinned here, because each one is a place the design can
silently degrade back into the thing it replaced:

1. the **terminal portal loop** — an outbox file reaches the TTY, a typed
   reply reaches the wake as a real event, and the accepted file is retired
   rather than deleted;
2. the **secrets seam** — a ``control:`` file is never printed as chat, the
   gate ceremony runs against the terminal, and its outcome comes back as
   an event (so no token can transit the model or ``.brr/traces/``);
3. the **degradation** — no TTY / no playbook means the mechanical install
   runs, with a line naming why, and never a blocking read on stdin.

The runner is always scripted: a fake that writes outbox files and reads
``inbox.json`` exercises the whole loop without a model.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from _helpers import init_git_repo
from brr import adopt, init_wake, portals, prompts, runner
from brr.runner import RunnerResult


# ── scaffolding ─────────────────────────────────────────────────────


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    for sub in ("inbox", "responses", "outbox", "gates", "runs", "traces"):
        (repo / ".brr" / sub).mkdir(parents=True, exist_ok=True)
    return repo


def _fake_result(invocation, returncode=0, stdout="done — receipt"):
    return RunnerResult(
        invocation=invocation,
        runner_name="mock-runner",
        command=["mock"],
        stdout=stdout,
        stderr="",
        returncode=returncode,
        trace_dir=None,
        artifacts=[],
    )


def _scripted_runner(script):
    """Build an ``invoke`` stand-in that runs *script(invocation)* inline.

    The script plays the wake: it writes outbox files (chat, control verbs)
    and may read ``inbox.json`` to see the user's replies. It runs on the
    session's runner thread, exactly like a real invocation would.
    """

    def _invoke(runner_name, invocation, cfg=None):
        script(invocation)
        return _fake_result(invocation)

    return _invoke


def _write_outbox(outbox: Path, name: str, text: str) -> None:
    """Stage-then-rename, the way a real wake writes a message."""
    tmp = outbox / f"{name}.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(outbox / name)


def _outbox_dir(repo: Path) -> Path:
    root = repo / ".brr" / "outbox"
    dirs = [p for p in root.iterdir() if p.is_dir()]
    assert len(dirs) == 1, dirs
    return dirs[0]


# ── portals ─────────────────────────────────────────────────────────


class TestPortals:
    def test_live_inbox_has_the_daemon_shape(self, tmp_path):
        out = tmp_path / "outbox"
        path = portals.write_live_inbox(out, "evt-1", [{"id": "evt-2"}])
        payload = json.loads(path.read_text())
        assert path.name == "inbox.json"
        assert payload["current_event"] == "evt-1"
        assert payload["events"] == [{"id": "evt-2"}]
        assert payload["version"] == 1 and payload["generated_at"]

    def test_init_capsule_says_unimplemented_not_absent(self, tmp_path):
        """A missing facet reads as 'not measured yet' and invites waiting."""
        capsule = portals.init_portal_state(
            current_event_id="evt-1", events=[], phase="interview",
        )
        path = portals.write_portal_state(tmp_path / "outbox", capsule)
        payload = json.loads(path.read_text())
        assert path.name == "portal-state.json"
        assert payload["events"] == [] and payload["notices"] == []
        assert payload["resources"]["quota"] == "unimplemented"
        assert payload["stage"] == "brnrd init wake"

    def test_daemon_still_writes_the_same_file(self, tmp_path):
        """The extraction must not move the daemon's file or its keys."""
        from brr import daemon

        inbox = tmp_path / "inbox"
        inbox.mkdir()
        out = tmp_path / "outbox"
        path = daemon._write_live_inbox(out, inbox, "evt-1")
        assert path.name == portals.LIVE_INBOX_NAME
        assert json.loads(path.read_text())["current_event"] == "evt-1"


# ── the runner doctor ───────────────────────────────────────────────


class TestRunnerDoctor:
    def test_shell_list_comes_from_the_catalog(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner.shutil, "which", lambda _n: None)
        diag = runner.diagnose_runners(tmp_path)
        assert diag.available == []
        # Every declared Shell family shows up without a second edit here.
        assert {"claude", "codex"} <= set(diag.shells_missing)

    def test_report_carries_all_three_lanes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner.shutil, "which", lambda _n: None)
        monkeypatch.setenv("PATH", "/usr/bin:/opt/x/bin")
        text = runner.render_runner_doctor(runner.diagnose_runners(tmp_path))
        # 1. what was checked — an observation about PATH, not the machine
        assert "what I checked" in text
        assert "/opt/x/bin" in text
        assert "not a claim they are absent" in text
        # 2. two recovery lanes
        assert "command -v claude" in text
        assert "fresh terminal" in text
        assert "brnrd runners list --all" in text
        assert runner.SHELL_HELP["codex"].docs_url in text
        # 3. the return path — re-running init, and nothing that needs a Runner
        assert "re-run `brnrd init`" in text
        assert "--auto" not in text

    def test_launch_failure_reuses_the_same_ladder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner.shutil, "which", lambda _n: None)
        text = runner.render_runner_doctor(
            runner.diagnose_runners(tmp_path),
            attempted="claude-opus",
            error="exit 127: command not found",
        )
        assert "claude-opus" in text and "command not found" in text
        assert "not installed yet" in text

    def test_auth_blocked_profile_is_named_separately(self, tmp_path, monkeypatch):
        """On PATH but unusable is a different problem than not on PATH."""
        monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        diag = runner.diagnose_runners(tmp_path)
        assert "claude-bare-api-only" in diag.auth_blocked
        assert diag.available  # the keyless profiles are still fine


# ── prompt assembly ─────────────────────────────────────────────────


class TestPromptAssembly:
    def test_stage_line_and_bootstrap_carveout(self, tmp_path):
        repo = _repo(tmp_path)
        prompt, _score = prompts.build_init_wake_prompt(
            repo,
            event_id="evt-1",
            response_path=str(repo / ".brr/responses/evt-1.md"),
            outbox_path=str(repo / ".brr/outbox/evt-1"),
            facts={"runner_name": "mock", "gh_available": False},
        )
        assert f"- Stage: {prompts.INIT_WAKE_STAGE}" in prompt
        # F4: the wake must be told the bootstrap exception explicitly, or it
        # fights the receipts pin that tells every other host run to branch.
        assert "current branch" in prompt and "Bootstrap exception" in prompt
        assert "Init facts" in prompt
        assert "gh CLI: no" in prompt

    def test_playbook_is_the_task(self, tmp_path):
        repo = _repo(tmp_path)
        prompt, _ = prompts.build_init_wake_prompt(
            repo, event_id="e", response_path="r", outbox_path="o",
        )
        assert "Init playbook" in prompt
        assert "the first wake" in prompt

    def test_daemon_stage_is_unchanged_by_default(self, tmp_path):
        repo = _repo(tmp_path)
        prompt = prompts.build_daemon_prompt("task", "evt-1", "/tmp/r.md", repo)
        assert "- Stage: brnrd daemon run" in prompt
        assert "Bootstrap exception" not in prompt

    def test_assembles_on_a_repo_with_no_account(self, tmp_path):
        """Minute zero is the *normal* state; injected blocks must degrade."""
        repo = _repo(tmp_path)
        prompt, score = prompts.build_init_wake_prompt(
            repo, event_id="e", response_path="r", outbox_path="o",
        )
        assert prompt and score is not None

    def test_playbook_availability_gates_the_path(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path)
        assert prompts.init_playbook_available(repo)
        monkeypatch.setattr(prompts, "read_prompt", lambda *a, **kw: "")
        assert not prompts.init_playbook_available(repo)


# ── the terminal portal loop ────────────────────────────────────────


class TestTerminalLoop:
    def test_message_reaches_the_tty_and_reply_becomes_an_event(self, tmp_path):
        repo = _repo(tmp_path)
        printed: list[str] = []
        seen_reply = threading.Event()

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            _write_outbox(outbox, "01-hello.md", "this is a Python CLI. Shall I?")
            # The wake polls its inbox the way linger discipline says to.
            for _ in range(200):
                payload = json.loads(
                    (outbox / "inbox.json").read_text()
                )
                if payload["events"]:
                    seen_reply.set()
                    break
                time.sleep(0.01)
            _write_outbox(outbox, "02-bye.md", "contract authored.")

        result = init_wake.run_init_wake(
            repo, "mock-runner",
            cfg={},
            invoke=_scripted_runner(script),
            writer=printed.append,
            reader=lambda: "yes, go ahead",
            poll_interval=0.01,
        )

        assert result.ok, result.error
        assert result.messages == 2
        assert result.replies == 2
        assert seen_reply.is_set(), "the reply never showed up in inbox.json"
        assert "this is a Python CLI. Shall I?" in printed
        # Accepted files are retired, never deleted — the content survives.
        processed = _outbox_dir(repo) / ".processed"
        assert {p.name for p in processed.iterdir()} == {
            "01-hello.md", "02-bye.md",
        }

    def test_silence_is_a_valid_answer(self, tmp_path):
        """A vanished user finishes the install; it is not an error."""
        repo = _repo(tmp_path)

        def script(invocation):
            _write_outbox(
                Path(invocation.env["BRR_OUTBOX_DIR"]), "01.md", "one question?",
            )

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(script),
            writer=lambda _t: None,
            reader=lambda: "",
            poll_interval=0.01,
        )
        assert result.ok and result.replies == 0

    def test_event_is_real_and_retired_at_closeout(self, tmp_path):
        """A real inbox event makes the whole portal grammar work unmodified —
        and a *pending* one left behind would re-wake a later `brnrd up`."""
        repo = _repo(tmp_path)
        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(lambda _i: None),
            writer=lambda _t: None, reader=lambda: "", poll_interval=0.01,
        )
        event = (repo / ".brr" / "inbox" / f"{result.event_id}.md").read_text()
        assert "source: init" in event
        assert "status: done" in event

    def test_runner_env_carries_the_portal_paths(self, tmp_path):
        repo = _repo(tmp_path)
        captured = {}

        def script(invocation):
            captured.update(invocation.env)

        init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(script),
            writer=lambda _t: None, reader=lambda: "", poll_interval=0.01,
        )
        assert captured["BRR_PORTAL_STATE"].endswith("portal-state.json")
        assert Path(captured["BRR_OUTBOX_DIR"]).is_dir()

    def test_silent_runner_is_a_failure_not_a_finished_wake(self, tmp_path):
        repo = _repo(tmp_path)

        def _invoke(runner_name, invocation, cfg=None):
            return None  # thread ended, nothing said, nothing written

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={}, invoke=_invoke,
            writer=lambda _t: None, reader=lambda: "", poll_interval=0.01,
        )
        assert not result.ok
        assert "never spoke" in (result.error or "")

    def test_runner_failure_is_reported_not_raised(self, tmp_path):
        repo = _repo(tmp_path)

        def _invoke(runner_name, invocation, cfg=None):
            raise RuntimeError("auth expired")

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={}, invoke=_invoke,
            writer=lambda _t: None, reader=lambda: "", poll_interval=0.01,
        )
        assert not result.ok and "auth expired" in result.error

    def test_card_is_captured_at_close(self, tmp_path):
        repo = _repo(tmp_path)

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            (outbox / ".card").write_text("## Now\ncontract authored\n")

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={}, invoke=_scripted_runner(script),
            writer=lambda _t: None, reader=lambda: "", poll_interval=0.01,
        )
        assert "contract authored" in result.card
        assert result.messages == 0, "the card is control state, never chat"


# ── the secrets seam ────────────────────────────────────────────────


class TestControlVerbs:
    def test_gate_setup_takes_the_terminal_and_reports_back(self, tmp_path):
        repo = _repo(tmp_path)
        printed: list[str] = []
        calls: list[str] = []

        def control(_repo_root, verb):
            calls.append(verb)
            return init_wake.ControlOutcome(verb, True, "authenticated as @bot")

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            _write_outbox(
                outbox, "01.md",
                "---\ncontrol: gate-setup telegram\n---\n"
                "(brnrd runs the token walk)",
            )
            for _ in range(200):
                events = json.loads((outbox / "inbox.json").read_text())["events"]
                if events:
                    break
                time.sleep(0.01)

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={}, invoke=_scripted_runner(script),
            control=control, writer=printed.append,
            reader=lambda: pytest.fail("a control verb must never prompt for chat"),
            poll_interval=0.01,
        )

        assert calls == ["gate-setup telegram"]
        assert result.gates_configured == ["telegram"]
        # The body of a control file is never delivered as a message.
        assert result.messages == 0
        assert not any("brnrd runs the token walk" in p for p in printed)
        # …and the outcome came back as an event the wake can react to.
        bodies = [
            p.read_text() for p in (repo / ".brr" / "inbox").iterdir()
        ]
        assert any("authenticated as @bot" in b for b in bodies)

    def test_gate_failure_is_parked_with_its_resume_command(self, tmp_path):
        repo = _repo(tmp_path)

        def boom(_brr_dir):
            raise RuntimeError("token rejected")

        class _FakeGate:
            setup = staticmethod(boom)

        import brr.cli as cli

        original = cli._load_gate
        try:
            cli._load_gate = lambda _name: _FakeGate
            result = init_wake.dispatch_control(repo, "gate-setup telegram")
        finally:
            cli._load_gate = original

        assert not result.ok
        assert "brnrd gate setup telegram" in result.detail

    def test_unknown_verb_is_explained_not_swallowed(self, tmp_path):
        repo = _repo(tmp_path)
        outcome = init_wake.dispatch_control(repo, "launch-missiles")
        assert not outcome.ok and "unknown control verb" in outcome.detail

    def test_unknown_gate_names_the_known_ones(self, tmp_path):
        repo = _repo(tmp_path)
        outcome = init_wake.dispatch_control(repo, "gate-setup smoke-signals")
        assert not outcome.ok and "telegram" in outcome.detail


# ── degradation and resume ──────────────────────────────────────────


class TestDegradation:
    def test_no_tty_means_no_wake(self, tmp_path):
        repo = _repo(tmp_path)
        ok, why = init_wake.wake_path_available(repo, interactive=False)
        assert not ok and "no TTY" in why

    def test_missing_playbook_degrades_with_a_reason(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path)
        monkeypatch.setattr(prompts, "read_prompt", lambda *a, **kw: "")
        ok, why = init_wake.wake_path_available(repo, interactive=True)
        assert not ok and "playbook" in why

    def test_env_escape_hatch(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path)
        monkeypatch.setenv("BRR_NO_INIT_WAKE", "1")
        ok, why = init_wake.wake_path_available(repo, interactive=True)
        assert not ok and "BRR_NO_INIT_WAKE" in why

    def test_init_without_a_tty_runs_the_mechanical_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The CI-safe path: no wake, no blocking read, one line saying why."""
        repo = tmp_path / "repo"
        repo.mkdir()
        init_git_repo(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(
            "brr.runner.detect_all_runners", lambda *a, **kw: ["mock-runner"],
        )
        seen: list[str] = []

        def _invoke(runner_name, invocation, cfg=None):
            seen.append(invocation.label)
            (repo / "AGENTS.md").write_text(
                "## Stewardship\n" + "x" * 200
                + "\n## Knowledge base\n\n## Guardrails\n"
            )
            return _fake_result(invocation)

        monkeypatch.setattr("brr.runner.invoke_runner", _invoke)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        adopt.init_repo()

        assert seen == ["setup"], "the wake must not run without a TTY"
        assert "no TTY on stdin" in capsys.readouterr().out

    def test_no_runner_prints_the_doctor(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        init_git_repo(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr("brr.runner.detect_all_runners", lambda *a, **kw: [])
        with pytest.raises(SystemExit) as excinfo:
            adopt.init_repo()
        assert "what I checked" in str(excinfo.value)
        assert "re-run `brnrd init`" in str(excinfo.value)

    def test_bootstrap_is_idempotent_so_resume_is_free(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        init_git_repo(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(
            "brr.runner.detect_all_runners", lambda *a, **kw: ["mock-runner"],
        )
        root, available = adopt.bootstrap()
        (root / ".brr" / "config").write_text("runner: pinned\n")
        again, _ = adopt.bootstrap()
        assert again == root
        assert "pinned" in (root / ".brr" / "config").read_text()


class TestWakeDispatchFromInit:
    def test_tty_path_dispatches_the_wake_and_verifies_after(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        init_git_repo(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(
            "brr.runner.detect_all_runners", lambda *a, **kw: ["mock-runner"],
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr(init_wake, "_default_reader", lambda *_a: "")

        def _invoke(runner_name, invocation, cfg=None):
            assert invocation.kind == "init"
            assert invocation.label.startswith("init-evt-")
            (repo / "AGENTS.md").write_text(
                "## Stewardship\n" + "x" * 200
                + "\n## Knowledge base\n\n## Guardrails\n"
            )
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            (outbox / ".card").write_text("## Now\ninterviewed, authored\n")
            return _fake_result(invocation)

        monkeypatch.setattr("brr.runner.invoke_runner", _invoke)

        adopt.init_repo()

        out = capsys.readouterr().out
        assert "handing this session to the agent" in out
        # brnrd still owns the post-passes: bridges + the structure gate.
        assert "✓ AGENTS.md" in out
        assert "interviewed, authored" in out
        assert "brnrd up" in out
