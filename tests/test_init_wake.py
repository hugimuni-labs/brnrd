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
import itertools
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


def _fake_result(
    invocation,
    returncode=0,
    stdout="done — receipt",
    stderr="",
):
    return RunnerResult(
        invocation=invocation,
        runner_name="mock-runner",
        command=["mock"],
        stdout=stdout,
        stderr=stderr,
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


def _portal(outbox: Path) -> dict:
    """The portal capsule as a *wake* would see it — read back off disk.

    Never the dict the session built: the claim under test is what a polling
    model can observe in the file, so every assertion goes through JSON.
    """
    for _ in range(50):  # the writer renames into place; a read can lose that race
        try:
            return json.loads(
                (outbox / portals.LIVE_PORTAL_STATE_NAME).read_text()
            )
        except (OSError, json.JSONDecodeError):
            time.sleep(0.01)
    raise AssertionError("portal-state.json never became readable")


def _session(repo: Path, **kwargs) -> "init_wake._Session":
    """A session with no runner thread started — for driving ``drain_once``."""
    kwargs.setdefault("cfg", {})
    kwargs.setdefault("invoke", _scripted_runner(lambda _i: None))
    kwargs.setdefault("writer", lambda _t: None)
    kwargs.setdefault("poll_interval", 0.01)
    return init_wake._Session(repo, "mock-runner", **kwargs)


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

    def test_change_token_moves_when_only_the_awaiting_flag_flips(self, tmp_path):
        """The token was ``str(len(events))`` — a token for one field.

        A wake polling the documented cheap check saw no delta when the
        human took the floor, which is precisely the moment it needed one.
        """
        out = tmp_path / "outbox"
        shape = {"current_event_id": "evt-1", "events": [], "phase": "interview"}
        portals.write_portal_state(out, portals.init_portal_state(**shape))
        idle = json.loads((out / "portal-state.json").read_text())
        portals.write_portal_state(
            out, portals.init_portal_state(**shape, awaiting_reply=True),
        )
        waiting = json.loads((out / "portal-state.json").read_text())

        assert idle["awaiting_reply"] is False
        assert waiting["awaiting_reply"] is True
        assert idle["events"] == waiting["events"] == []
        assert idle["change_token"] != waiting["change_token"]

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

    def test_github_identity_fact_renders_when_present(self, tmp_path):
        """`adopt._state_identity()` resolves this once and passes it in —
        the facts block is where the wake reads it back, no second `gh`
        shell-out on its side."""
        repo = _repo(tmp_path)
        prompt, _score = prompts.build_init_wake_prompt(
            repo,
            event_id="evt-1",
            response_path=str(repo / ".brr/responses/evt-1.md"),
            outbox_path=str(repo / ".brr/outbox/evt-1"),
            facts={"runner_name": "mock", "github_identity": "octocat"},
        )
        assert "GitHub identity (via gh): octocat" in prompt

    def test_github_identity_fact_absent_when_not_resolved(self, tmp_path):
        repo = _repo(tmp_path)
        prompt, _score = prompts.build_init_wake_prompt(
            repo,
            event_id="evt-1",
            response_path=str(repo / ".brr/responses/evt-1.md"),
            outbox_path=str(repo / ".brr/outbox/evt-1"),
            facts={"runner_name": "mock"},
        )
        assert "GitHub identity" not in prompt

    def test_playbook_is_the_task(self, tmp_path):
        repo = _repo(tmp_path)
        prompt, _ = prompts.build_init_wake_prompt(
            repo, event_id="e", response_path="r", outbox_path="o",
        )
        assert "Init playbook" in prompt
        assert "the first wake" in prompt

    def test_playbook_names_the_channel_a_reply_comes_back_on(self, tmp_path):
        """The defect's other half: the task never said where to look.

        The only line the playbook had about the human's side was the
        failure-honesty one ("no reply on a beat ⇒ take defaults"), so a
        model that asked a question had nowhere to wait and correctly
        concluded the user had vanished.
        """
        repo = _repo(tmp_path)
        prompt, _ = prompts.build_init_wake_prompt(
            repo, event_id="e", response_path="r", outbox_path="o",
        )
        flat = " ".join(prompt.split())  # the wrapping is prose, not contract
        # the channel, by the same names the portal vocabulary already uses
        assert "How their answer reaches you" in flat
        assert "new pending event" in flat
        assert "inbox.json" in flat and "portal-state.json" in flat
        # the fact that replaces the guess, and a floor with a number on it
        assert "awaiting_reply" in flat
        assert "90 seconds" in flat
        # …and the give-up line now points at that floor instead of being
        # the whole story.
        assert "past the floor" in flat

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


# ── facts collection ────────────────────────────────────────────────


class TestCollectFacts:
    def test_facts_carry_the_install_shape(self, tmp_path, monkeypatch):
        """The playbook opens on the shape; the shape has to be in the bundle.

        A prompt that keys on a fact the facts block never carries is the
        contract drift #1117 was filed for — a promise the code cannot
        keep. So the three openings and the keys they read move together.

        Absent means *unknown*, never *no*: an unpaired repo omits the key
        rather than asserting ``False``, because the openings key on
        presence and a failed read must degrade to the local opening
        rather than claim a shape it cannot see.
        """
        repo = _repo(tmp_path)

        facts = init_wake.collect_facts(repo, runner_name="mock-runner")
        assert "account_paired" not in facts, "unpaired must be absent, not False"
        assert "docker_available" in facts

        from brr import account as account_mod

        monkeypatch.setattr(
            account_mod, "_connected_account_id", lambda _root: "acc_123",
        )
        assert init_wake.collect_facts(repo, runner_name="mock-runner")[
            "account_paired"
        ] is True

    def test_github_identity_passthrough_costs_no_extra_gh_call(
        self, tmp_path, monkeypatch,
    ):
        """`collect_facts` takes the identity as a parameter — `adopt`
        already paid the one `gh api user` round trip stating it on the
        terminal, and this must not pay it again."""
        from brr import home_link

        monkeypatch.setattr(home_link, "gh_available", lambda: True)
        monkeypatch.setattr(
            home_link, "resolve_owner",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                AssertionError("collect_facts must not re-resolve the owner"),
            ),
        )
        repo = _repo(tmp_path)
        facts = init_wake.collect_facts(
            repo, runner_name="mock", github_identity="octocat",
        )
        assert facts["github_identity"] == "octocat"

    def test_github_identity_omitted_when_none(self, tmp_path):
        repo = _repo(tmp_path)
        facts = init_wake.collect_facts(repo, runner_name="mock")
        assert "github_identity" not in facts


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

    def test_a_flooded_stdin_stops_being_asked(self, tmp_path):
        """#1107: `yes ''` into the interview used to spin at memory speed.

        Found by the 2026-08-04 OOM incident (#1104): a strand verified the
        interview with `{ printf 'just do defaults\\n\\n'; yes ''; } | script
        -qec "brnrd init ..."` and the loop consumed empty lines until the
        kernel killed the host at 10.4 GB. The harness had no timeout, which
        was the trigger — but a prompt loop that cannot tell a pipe from a
        person is the defect, and the wall clock cannot see it: no time
        passes, so the abandoned-prompt ceiling never fires.

        The read here is unbounded on purpose, exactly like the pipe. What
        is asserted is that it stops being *called*.
        """
        repo = _repo(tmp_path)
        reads = itertools.count()

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            for index in range(12):
                _write_outbox(outbox, f"{index:02d}.md", f"question {index}?")

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(script),
            writer=lambda _t: None,
            reader=lambda: (next(reads), "")[1],   # forever empty, never blocks
            poll_interval=0.01,
        )

        assert result.ok, result.error
        assert result.degraded_to_defaults, (
            "the interview kept asking a pipe that never answers"
        )
        # Bounded, and by the constant rather than by a magic number — the
        # count is what separates "a person skipped a question" from "there
        # is nobody there", so it is the thing under test.
        assert next(reads) <= init_wake.EMPTY_READS_BEFORE_DEGRADING, (
            f"read {next(reads)} times for a cap of "
            f"{init_wake.EMPTY_READS_BEFORE_DEGRADING}"
        )

    def test_one_skipped_question_is_still_an_answer(self, tmp_path):
        """The counter resets, so skipping mid-conversation costs nothing.

        Without this the guard would degrade a real interview: "sending
        nothing skips the question" is the documented affordance, and three
        skips spread across a conversation are a person using it, not a
        pipe. Neutering the reset makes this go red while the test above
        stays green — which is the pair that pins the distinction.
        """
        repo = _repo(tmp_path)
        replies = iter(["", "yes", "", "no", "", "sure"])

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            for index in range(6):
                _write_outbox(outbox, f"{index:02d}.md", f"question {index}?")

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(script),
            writer=lambda _t: None,
            reader=lambda: next(replies, ""),
            poll_interval=0.01,
        )

        assert result.ok, result.error
        assert not result.degraded_to_defaults, (
            "a person alternating skips with answers was read as a pipe"
        )
        assert result.replies == 3

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

    def test_runner_nonzero_exit_preserves_its_authentication_stderr(self, tmp_path):
        repo = _repo(tmp_path)

        def _invoke(runner_name, invocation, cfg=None):
            return _fake_result(
                invocation,
                returncode=1,
                stdout="",
                stderr="Not logged in. Run 'codex login' first.",
            )

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={}, invoke=_invoke,
            writer=lambda _t: None, reader=lambda: "", poll_interval=0.01,
        )

        assert not result.ok
        assert result.error == "Not logged in. Run 'codex login' first."

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


# ── "the human is composing" ────────────────────────────────────────


class TestAwaitingReply:
    """The window the wake could not see into.

    Between the model's question and the human's blank line, nothing used to
    change on disk: a user typing two lines looked exactly like a user who
    had walked away, and a well-behaved wake shipped defaults over a reply
    that was seconds from landing. The flag is that window, published.
    """

    def test_the_flag_is_on_disk_while_the_reader_blocks(self, tmp_path):
        repo = _repo(tmp_path)
        snapshots: list[dict] = []
        replies = iter(["", "kb/ in the repo\nand pytest is the gate"])

        def reader() -> str:
            # Read *while blocked* — this is the exact instant the model is
            # polling in, and the only one that matters.
            snapshots.append(_portal(session.outbox_dir))
            return next(replies)

        session = _session(repo, reader=reader)
        session.refresh_portals("dispatch")

        _write_outbox(session.outbox_dir, "01.md", "where should memory live?")
        assert session.drain_once() == 1
        quiet = _portal(session.outbox_dir)

        _write_outbox(session.outbox_dir, "02.md", "and what gates it?")
        assert session.drain_once() == 1
        landed = _portal(session.outbox_dir)

        assert snapshots[0]["awaiting_reply"] is True
        # A skipped beat lowers it again: no event, nobody waiting.
        assert quiet["awaiting_reply"] is False and quiet["events"] == []
        # `quiet` and the second blocked snapshot differ in the flag and in
        # nothing else — same phase, same empty event list — so the token
        # delta below is the flag's alone, not the event count's.
        assert snapshots[1]["awaiting_reply"] is True
        assert snapshots[1]["phase"] == quiet["phase"] == "interview"
        assert snapshots[1]["events"] == quiet["events"] == []
        assert snapshots[1]["change_token"] != quiet["change_token"]
        # …and the reply that landed is a real pending event again.
        assert landed["awaiting_reply"] is False
        assert len(landed["events"]) == 1
        assert landed["change_token"] != snapshots[1]["change_token"]

    def test_a_polling_wake_sees_the_flip_from_its_own_thread(self, tmp_path):
        """The real shape: the model polls the portal while the TTY blocks."""
        repo = _repo(tmp_path)
        observed: list[dict] = []
        released = threading.Event()

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            before = _portal(outbox)
            _write_outbox(outbox, "01.md", "which shape for memory?")
            try:
                for _ in range(500):
                    state = _portal(outbox)
                    if state["awaiting_reply"]:
                        observed.append((before, state))
                        return
                    time.sleep(0.01)
            finally:
                released.set()  # never leave the reader blocked on a failure

        def reader() -> str:
            # Stay on the floor until the wake has had its look, then answer.
            released.wait(10)
            return "kb/ in the repo"

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(script),
            writer=lambda _t: None, reader=reader, poll_interval=0.01,
        )

        assert observed, "the wake polled and never saw that a human had the floor"
        before, waiting = observed[0]
        assert before["awaiting_reply"] is False
        # A poller keyed on the token — the documented cheap check — sees it.
        assert waiting["change_token"] != before["change_token"]
        assert result.replies == 1
        final = _portal(_outbox_dir(repo))
        assert final["awaiting_reply"] is False
        assert final["phase"] == "closed"

    @pytest.mark.parametrize("boom", [EOFError, KeyboardInterrupt])
    def test_the_flag_clears_when_the_terminal_goes_away(self, tmp_path, boom):
        """^D and ^C are exits, not perpetual waiting."""
        repo = _repo(tmp_path / boom.__name__)

        def reader() -> str:
            raise boom()

        session = _session(repo, reader=reader)
        session.refresh_portals("dispatch")
        _write_outbox(session.outbox_dir, "01.md", "still there?")
        session.drain_once()

        state = _portal(session.outbox_dir)
        assert state["awaiting_reply"] is False
        assert state["events"] == []

    def test_the_flag_clears_when_the_reader_itself_breaks(self, tmp_path):
        """An unexpected error must not strand the portal claiming a listener."""
        repo = _repo(tmp_path)

        def reader() -> str:
            raise RuntimeError("the terminal fell over")

        session = _session(repo, reader=reader)
        _write_outbox(session.outbox_dir, "01.md", "still there?")
        with pytest.raises(RuntimeError):
            session.drain_once()
        assert _portal(session.outbox_dir)["awaiting_reply"] is False

    def test_the_prompt_states_how_to_send(self, tmp_path, monkeypatch, capsys):
        """He pressed Enter, nothing happened, and pressed Enter again."""
        repo = _repo(tmp_path)
        monkeypatch.setattr("builtins.input", lambda: "")

        session = _session(repo, reader=None)  # the real terminal reader
        _write_outbox(session.outbox_dir, "01.md", "what is this repo?")
        session.drain_once()
        _write_outbox(session.outbox_dir, "02.md", "and how is it checked?")
        session.drain_once()

        out = capsys.readouterr().out
        first = out.index(init_wake.FIRST_PROMPT)
        later = out.index(init_wake.NEXT_PROMPT)
        assert first < later, "the full rule belongs on the first beat"
        # The rule itself: a blank line sends, i.e. Enter twice.
        assert "blank line" in init_wake.FIRST_PROMPT
        assert "Enter twice" in init_wake.FIRST_PROMPT
        # Every later beat still carries it, without becoming a paragraph.
        assert "blank line" in init_wake.NEXT_PROMPT
        assert len(init_wake.NEXT_PROMPT) <= 32


# ── the clock that stops for a human (#1036) ────────────────────────
#
# All three tests drive the real session loop (`run_init_wake` /
# `_Session.run`) — the caller the defect actually lives in — with
# `time.monotonic` replaced by a test-controlled clock so "a human took N
# seconds to answer" is a deterministic assignment, not a real sleep. The
# background runner thread runs on real wall-clock time and only touches
# the outbox/inbox files; only the *reader* (the human's side) advances the
# fake clock, exactly where the real defect lived.


class TestDeadlineAndHumanTime:
    def test_reply_that_just_landed_is_always_processed(self, tmp_path, monkeypatch):
        """Rec 3: the tick right after a reply lands never kills the wake.

        The clock jump lives in ``writer`` — called synchronously, on the
        main thread, the instant *before* ``_offer_reply`` captures the
        start of the awaiting window (see ``drain_once``:
        ``self.writer(body); self._offer_reply()``) — so it lands as work
        time, not thinking time: rec 1's accounting alone gives this tick no
        slack (awaiting time this round is ~0, and the budget is exactly
        spent). Only rec 3's skip saves it. No thread races: the bump is on
        the same thread, in the statement right before the read it protects.
        """
        repo = _repo(tmp_path)
        clock = [0.0]
        monkeypatch.setattr(init_wake.time, "monotonic", lambda: clock[0])

        def writer(_body):
            # The wake's own work burned the whole 1800s budget before it
            # even finished printing this question — none of this is
            # "awaiting"; the flag isn't up yet.
            clock[0] = 1800.0

        def reader():
            return "go ahead"  # answers instantly: ~0 thinking time

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            _write_outbox(outbox, "01.md", "shall I proceed?")
            for _ in range(500):
                events = json.loads((outbox / "inbox.json").read_text())["events"]
                if events:
                    break
                time.sleep(0.01)
            # The thread ends right here — no tick after the reply lands
            # other than the one under test.

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(script),
            writer=writer, reader=reader,
            timeout_seconds=1800, poll_interval=0.01,
        )

        assert result.replies == 1
        assert result.ok, result.error

    def test_time_spent_awaiting_a_reply_does_not_count_against_the_budget(
        self, tmp_path, monkeypatch,
    ):
        """Rec 1: thinking time is excluded from the wake's own budget.

        Three rounds each "think" for 500s against a 100s budget — total
        elapsed clock (1500s) is 15x the budget, and every round's reply
        lands exactly when rec 3 would otherwise skip the check, so the
        pass here is rec 1's accounting, not rec 3's skip: the loop keeps
        ticking (via the trailing sleep below) past the last reply, on a
        tick where `just_replied` is False, and the deadline still holds.
        """
        repo = _repo(tmp_path)
        clock = [0.0]
        monkeypatch.setattr(init_wake.time, "monotonic", lambda: clock[0])

        def reader():
            clock[0] += 500.0
            return "thought about it"

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            for i in range(3):
                _write_outbox(outbox, f"{i:02d}.md", f"question {i}?")
                for _ in range(500):
                    events = json.loads(
                        (outbox / "inbox.json").read_text()
                    )["events"]
                    if len(events) > i:
                        break
                    time.sleep(0.01)
            # A beat with nothing pending, so the loop ticks the deadline
            # check at least once with `just_replied` False.
            time.sleep(0.05)

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(script),
            writer=lambda _t: None, reader=reader,
            timeout_seconds=100, poll_interval=0.01,
        )

        assert result.replies == 3
        assert result.ok, result.error

    def test_abandoned_prompt_ceiling_still_fires(self, tmp_path, monkeypatch):
        """Rec 1's stated cost: excluding thinking time needs its own,
        much longer ceiling, or a terminal nobody ever answers holds the
        run slot forever. Silence (never a landed reply) past that ceiling
        still kills the wake, even though the ordinary budget — pushed out
        by the same awaiting time — never would.
        """
        repo = _repo(tmp_path)
        clock = [0.0]
        monkeypatch.setattr(init_wake.time, "monotonic", lambda: clock[0])
        release = threading.Event()

        def reader():
            clock[0] += 1000.0  # far past the tiny test ceiling below
            return ""  # never actually answers — this is the abandonment

        def script(invocation):
            outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
            _write_outbox(outbox, "01.md", "still there?")
            release.wait(5)  # keep the thread alive for the loop to tick

        result = init_wake.run_init_wake(
            repo, "mock-runner", cfg={},
            invoke=_scripted_runner(script),
            writer=lambda _t: None, reader=reader,
            timeout_seconds=100_000,       # the ordinary budget never fires
            abandoned_prompt_seconds=500,  # small ceiling for the test
            poll_interval=0.01,
        )
        release.set()

        assert not result.ok
        assert "abandoned" in (result.error or "")
        assert result.replies == 0


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
        # the closing channel menu (#1084 family) — points at a door
        # (account connect / gate setup telegram), then lists upgrades;
        # see test_adopt.py::TestChannelMenu for the full pin.
        assert "brnrd account connect" in out
        assert "brnrd gate setup telegram" in out

    def test_failed_wake_stops_before_verification_and_next_step(
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
            return _fake_result(
                invocation,
                returncode=1,
                stdout="",
                stderr="Not logged in. Run 'codex login' first.",
            )

        monkeypatch.setattr("brr.runner.invoke_runner", _invoke)

        with pytest.raises(SystemExit) as excinfo:
            adopt.init_repo()

        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "Not logged in. Run 'codex login' first." in out
        assert "AGENTS.md missing" not in out
        assert "next: `brnrd up`" not in out
