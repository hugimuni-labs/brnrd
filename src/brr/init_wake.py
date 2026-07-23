"""The init wake — ``brnrd init``'s agentic half (#507, spec §3).

The mechanical bootstrap (``.brr/`` tree, dominion, runner detection) stays
in :mod:`adopt`. What this module does is hand the *session* to the agent:
one real inbox event, the portal files a wake expects, one runner
invocation, and a terminal loop that plays the daemon's part for exactly
one run — drain the outbox to the TTY, feed typed replies back as events,
and take the terminal back when the wake asks for a secret.

Why not boot a daemon: the wake needs prompt assembly plus portals, not the
run lifecycle. ``daemon._run_worker`` is ~1400 lines of event weaving,
branch planning, retry, presence, ledger, and delivery threads — a second
lifecycle to maintain for one invocation. **Delivery pre-gate is the
terminal**, and that is the only thing init has to implement itself.

The secrets seam (spec §4, F2) is the load-bearing bit: the wake
*orchestrates and explains*, brnrd *collects*. A ``control: gate-setup
telegram`` outbox file is not chat — it transfers the TTY to brnrd, which
runs the existing interactive ``auth``/``bind`` verbatim and posts the
outcome back as an event. Raw tokens therefore never enter the model
transcript or ``.brr/traces/``, and the gate modules need zero changes.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config as conf
from . import gitops
from . import portals
from . import prompts
from . import protocol
from . import runner as runner_mod

#: The contract body of the synthesized init event. Short on purpose — the
#: playbook is the task; this is the addressable *event* the whole portal
#: grammar (``event:`` replies, ``inbox.json`` pending lists, outbox
#: routing) hangs off, and it works unmodified because it is a real one.
INIT_CONTRACT = (
    "Initialize this repository — follow the init playbook.\n\n"
    "The person who ran `brnrd init` is at the terminal now. Talk to them "
    "through the outbox; they answer through this inbox."
)

#: Default wall clock for the whole wake. Generous: a real interview with a
#: human on the other side is minutes of thinking time, and the failure mode
#: of a short budget here is killing the product's first conversation.
DEFAULT_TIMEOUT_SECONDS = 1800

_POLL_INTERVAL = 1.0
_CARD_NAME = ".card"
_KEEPALIVE_NAME = ".keepalive"
_NAME_NAME = ".name"

#: Outbox dotfiles that are control surfaces, never chat.
_CONTROL_FILES = frozenset({_CARD_NAME, _KEEPALIVE_NAME, _NAME_NAME})


@dataclass
class InitWakeResult:
    """What the wake left behind. Every field is independently useful."""

    ok: bool
    event_id: str
    messages: int = 0
    replies: int = 0
    controls: list[str] = field(default_factory=list)
    gates_configured: list[str] = field(default_factory=list)
    card: str | None = None
    reply: str | None = None
    error: str | None = None
    aborted: bool = False


# ── Control verbs (the secrets seam) ────────────────────────────────


@dataclass
class ControlOutcome:
    """Result of a TTY handback, phrased for the wake to read as an event."""

    verb: str
    ok: bool
    detail: str


def _run_gate_setup(repo_root: Path, gate: str) -> ControlOutcome:
    """Run the *existing* interactive gate ceremony against the real TTY.

    Verbatim reuse — ``setup()`` when the gate defines one, else
    ``auth()`` + ``bind()``, exactly as ``cli.cmd_setup`` composes them.
    Each gate saves its state immediately, so an abort mid-walk still
    leaves the gates already finished working.
    """
    from . import cli

    if gate not in cli.GATES:
        return ControlOutcome(
            f"gate-setup {gate}", False,
            f"unknown gate {gate!r}; known gates: {', '.join(cli.GATES)}",
        )
    brr_dir = gitops.shared_brr_dir(repo_root)
    try:
        gate_mod = cli._load_gate(gate)
        setup = getattr(gate_mod, "setup", None)
        if setup is not None:
            setup(brr_dir)
        else:
            gate_mod.auth(brr_dir)
            gate_mod.bind(brr_dir)
    except (KeyboardInterrupt, EOFError):
        return ControlOutcome(
            f"gate-setup {gate}", False,
            "the user interrupted the walk; the gate is parked, not dropped "
            f"— it can be finished later with `brnrd gate setup {gate}`",
        )
    except Exception as exc:  # noqa: BLE001 — a failed gate is conversation, not a crash
        return ControlOutcome(
            f"gate-setup {gate}", False,
            f"{type(exc).__name__}: {exc}. Park it: the exact resume command "
            f"is `brnrd gate setup {gate}`",
        )
    from .gates import runtime as gate_runtime

    configured = gate_runtime.configured_gates(brr_dir)
    ok = gate in configured
    return ControlOutcome(
        f"gate-setup {gate}",
        ok,
        f"{gate} configured" if ok
        else f"{gate} walk finished but no state was saved — treat as parked",
    )


def _run_home_link(repo_root: Path) -> ControlOutcome:
    """Wire the dominion + knowledge repos to private GitHub repos."""
    from . import home_link

    if not home_link.gh_available():
        return ControlOutcome(
            "home-link", False,
            "`gh` is not on PATH, so GitHub durability can't be wired from "
            "here. Memory still works locally; this is deferrable.",
        )
    try:
        results = home_link.link_home(repo_root, conf.load_config(repo_root))
    except home_link.HomeLinkError as exc:
        return ControlOutcome("home-link", False, f"skipped: {exc}")
    except (KeyboardInterrupt, EOFError):
        return ControlOutcome("home-link", False, "interrupted by the user")
    detail = "; ".join(
        f"{r.slot}: {r.action} → {r.remote_url}"
        f" ({'pushed' if r.pushed else 'already up to date'})"
        for r in results
    ) or "nothing to link"
    return ControlOutcome("home-link", True, detail)


def dispatch_control(repo_root: Path, verb: str) -> ControlOutcome:
    """Route one ``control:`` value to its TTY handback."""
    parts = verb.split()
    head = parts[0] if parts else ""
    if head == "gate-setup" and len(parts) >= 2:
        return _run_gate_setup(repo_root, parts[1])
    if head == "home-link":
        return _run_home_link(repo_root)
    return ControlOutcome(
        verb, False,
        f"unknown control verb {verb!r} — known: `gate-setup <name>`, "
        "`home-link`",
    )


# ── The terminal portal loop ────────────────────────────────────────


def _default_reader(prompt: str = "you> ") -> str:
    """Read a multi-line reply from the TTY; a blank line ends it."""
    print(prompt, end="", flush=True)
    lines: list[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _outbox_messages(outbox_dir: Path) -> list[Path]:
    """Deliverable files in the outbox, oldest first.

    Same accepted-file discipline the daemon drain uses: ``.tmp`` staging
    is invisible (the rename is the commit), control files are not chat,
    and ordering is by mtime so the wake's own sequence survives.
    """
    if not outbox_dir.exists():
        return []
    try:
        entries = sorted(
            (p for p in outbox_dir.iterdir() if p.is_file()),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
    except OSError:
        return []
    out: list[Path] = []
    for path in entries:
        if portals.is_staging_name(path.name):
            continue
        if path.name in portals.CONTROL_NAMES or path.name in _CONTROL_FILES:
            continue
        if path.name.startswith("."):
            continue
        out.append(path)
    return out


def _retire(path: Path) -> None:
    """Move an accepted file aside — never delete a message's content."""
    try:
        target_dir = path.parent / ".processed"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if target.exists():
            target = target_dir / f"{time.time_ns()}-{path.name}"
        path.replace(target)
    except OSError:
        pass


def _keepalive_deadline(outbox_dir: Path, default_deadline: float) -> float:
    """Honour ``.keepalive`` as a timeout extension, exactly as a run does."""
    path = outbox_dir / _KEEPALIVE_NAME
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return default_deadline
    if not raw:
        return default_deadline
    from . import schedule as schedule_mod

    first = raw.splitlines()[0].strip()
    if first.startswith("+"):
        secs = schedule_mod.parse_duration(first[1:].strip())
        if secs is None:
            return default_deadline
        try:
            return max(default_deadline, path.stat().st_mtime + secs)
        except OSError:
            return default_deadline
    until = schedule_mod.parse_iso(first)
    return max(default_deadline, until) if until else default_deadline


class _Session:
    """One init wake: portals, the runner thread, and the terminal loop."""

    def __init__(
        self,
        repo_root: Path,
        runner_name: str,
        *,
        cfg: dict[str, Any] | None = None,
        facts: dict[str, Any] | None = None,
        reader: Callable[[], str] | None = None,
        writer: Callable[[str], None] | None = None,
        invoke: Callable[..., Any] | None = None,
        control: Callable[[Path, str], ControlOutcome] | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        poll_interval: float = _POLL_INTERVAL,
        interactive: bool = True,
    ) -> None:
        self.repo_root = repo_root
        self.runner_name = runner_name
        self.cfg = cfg if cfg is not None else conf.load_config(repo_root)
        self.facts = facts or {}
        self.reader = reader or _default_reader
        self.writer = writer or (lambda text: print(text, flush=True))
        self.invoke = invoke or runner_mod.invoke_runner
        self.control = control or dispatch_control
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval
        self.interactive = interactive

        brr_dir = gitops.shared_brr_dir(repo_root)
        self.brr_dir = brr_dir
        self.inbox_dir = brr_dir / "inbox"
        self.responses_dir = brr_dir / "responses"
        self.event_path = protocol.create_event(
            self.inbox_dir, "init", INIT_CONTRACT,
        )
        self.event_id = self.event_path.stem
        self.outbox_dir = brr_dir / "outbox" / self.event_id
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        self.response_path = protocol.response_path(
            self.responses_dir, self.event_id,
        )
        self.proc_label = f"init-{self.event_id}"
        self.result = InitWakeResult(ok=False, event_id=self.event_id)
        self._abort = threading.Event()
        self._runner_result: Any = None
        self._runner_error: BaseException | None = None

    # ── portals ────────────────────────────────────────────────

    def _pending_for_wake(self) -> list[dict[str, Any]]:
        """Every pending event that is not the wake's own contract.

        Init has exactly one visibility rule; the daemon's worker/respawn/
        dispatch-edge carveouts describe a lifecycle init does not have.
        """
        return [
            {
                "id": str(ev.get("id") or ""),
                "source": str(ev.get("source") or ""),
                "summary": str(ev.get("body") or "")[:280],
            }
            for ev in protocol.list_pending(self.inbox_dir)
            if ev.get("id") != self.event_id
        ]

    def refresh_portals(self, phase: str) -> None:
        events = self._pending_for_wake()
        portals.write_live_inbox(self.outbox_dir, self.event_id, events)
        portals.write_portal_state(
            self.outbox_dir,
            portals.init_portal_state(
                current_event_id=self.event_id,
                events=events,
                phase=phase,
                change_token=str(len(events)),
            ),
        )

    def post_event(self, body: str, **meta: object) -> str:
        """Put a user reply (or a control outcome) into the wake's inbox."""
        path = protocol.create_event(self.inbox_dir, "init", body, **meta)
        self.refresh_portals("interview")
        return path.stem

    # ── the runner ──────────────────────────────────────────────

    def _build_invocation(self) -> "runner_mod.RunnerInvocation":
        prompt, _score = prompts.build_init_wake_prompt(
            self.repo_root,
            event_id=self.event_id,
            response_path=str(self.response_path),
            outbox_path=str(self.outbox_dir),
            facts=self.facts,
            runner_name=self.runner_name,
            runner_medium=self.runner_name,
            budget_seconds=self.timeout_seconds,
            runtime_dir=str(self.brr_dir),
            event_body=INIT_CONTRACT,
        )
        return runner_mod.RunnerInvocation(
            kind="init",
            # The label *is* the kill address (``runner._active_procs`` keys
            # on it), so it carries the event id: an interrupt must reach
            # this wake's process and nothing else's.
            label=self.proc_label,
            prompt=prompt,
            cwd=self.repo_root,
            repo_root=self.repo_root,
            response_path=str(self.response_path),
            timeout_seconds=self.timeout_seconds,
            env={
                "BRR_PORTAL_STATE": str(
                    self.outbox_dir / portals.LIVE_PORTAL_STATE_NAME
                ),
                "BRR_OUTBOX_DIR": str(self.outbox_dir),
                "BRR_EVENT_ID": self.event_id,
            },
        )

    def _runner_thread(self) -> None:
        try:
            self._runner_result = self.invoke(
                self.runner_name, self._build_invocation(), cfg=self.cfg,
            )
        except BaseException as exc:  # noqa: BLE001 — reported, not raised, across the thread
            self._runner_error = exc

    # ── the drain ───────────────────────────────────────────────

    def drain_once(self) -> int:
        """Print/handle every outbox file waiting right now. Returns count."""
        handled = 0
        for path in _outbox_messages(self.outbox_dir):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, body = protocol.parse_outbox_message(text)
            _retire(path)
            handled += 1
            verb = str(meta.get("control") or "").strip()
            if verb:
                self._handle_control(verb)
                continue
            self.result.messages += 1
            self.writer(body.strip())
            self._offer_reply()
        return handled

    def _handle_control(self, verb: str) -> None:
        """Take the terminal back, run the ceremony, tell the wake what happened."""
        self.result.controls.append(verb)
        self.refresh_portals(f"control:{verb}")
        self.writer(f"[brnrd] {verb} — taking the terminal for this step")
        outcome = self.control(self.repo_root, verb)
        if outcome.ok and outcome.verb.startswith("gate-setup "):
            self.result.gates_configured.append(outcome.verb.split()[-1])
        self.writer(f"[brnrd] {outcome.verb}: {outcome.detail}")
        self.post_event(
            f"control outcome — {outcome.verb}: "
            f"{'ok' if outcome.ok else 'failed'}\n\n{outcome.detail}",
            control_outcome=outcome.verb,
        )

    def _offer_reply(self) -> None:
        """Give the human the floor; silence is a valid answer.

        A vanished user is not an error: the playbook's own failure-honesty
        rule says take defaults and finish the install. An empty reply here
        posts nothing, so the wake sees no new event and proceeds.
        """
        if not self.interactive:
            return
        try:
            reply = self.reader()
        except (EOFError, KeyboardInterrupt):
            return
        if not reply.strip():
            return
        self.result.replies += 1
        self.post_event(reply.strip(), reply_to=self.event_id)

    # ── run ─────────────────────────────────────────────────────

    def run(self) -> InitWakeResult:
        self.refresh_portals("dispatch")
        previous_sigint = None
        if threading.current_thread() is threading.main_thread():
            try:
                previous_sigint = signal.signal(signal.SIGINT, self._on_sigint)
            except (ValueError, OSError):
                previous_sigint = None
        thread = threading.Thread(
            target=self._runner_thread, name="init-wake-runner", daemon=True,
        )
        thread.start()
        start = time.monotonic()
        try:
            while thread.is_alive():
                self.drain_once()
                if self._abort.is_set():
                    break
                thread.join(self.poll_interval)
                # Recomputed each tick rather than carried: ``.keepalive`` is
                # wall-clock and can be rewritten mid-wake, so the extension
                # is translated into this loop's monotonic clock every time
                # instead of being frozen at one reading.
                deadline = start + self.timeout_seconds
                extended = _keepalive_deadline(self.outbox_dir, 0.0)
                if extended:
                    deadline = max(
                        deadline,
                        time.monotonic() + (extended - time.time()),
                    )
                if time.monotonic() >= deadline:
                    self.result.error = (
                        f"the init wake outlived its {self.timeout_seconds}s "
                        "budget"
                    )
                    self._kill_runner()
                    break
            # Whatever the wake said on its way out still counts.
            self.drain_once()
        finally:
            if previous_sigint is not None:
                try:
                    signal.signal(signal.SIGINT, previous_sigint)
                except (ValueError, OSError):
                    pass
        return self._closeout()

    def _on_sigint(self, _signum, _frame) -> None:
        self.result.aborted = True
        self._abort.set()
        self._kill_runner()

    def _kill_runner(self) -> None:
        try:
            runner_mod.kill_matching(self.proc_label)
        except Exception:  # noqa: BLE001 — best effort; the thread is a daemon
            pass

    def _closeout(self) -> InitWakeResult:
        card_path = self.outbox_dir / _CARD_NAME
        if card_path.exists():
            try:
                self.result.card = card_path.read_text(encoding="utf-8")
            except OSError:
                pass
        reply = protocol.read_response(self.responses_dir, self.event_id)
        if not reply and self._runner_result is not None:
            reply = getattr(self._runner_result, "stdout", "") or None
        self.result.reply = (reply or "").strip() or None

        if self._runner_error is not None:
            self.result.error = (
                f"{type(self._runner_error).__name__}: {self._runner_error}"
            )
        elif self._runner_result is not None:
            rc = getattr(self._runner_result, "returncode", 0)
            if rc not in (0, None):
                self.result.error = self.result.error or (
                    f"runner exited {rc}"
                )
        elif not self.result.aborted and self.result.error is None:
            # Thread ended with no result and no exception, no outbox, no
            # response: silence is a runner failure, not a finished wake.
            if not self.result.messages and not self.result.reply:
                self.result.error = "the runner never spoke and produced nothing"

        self.result.ok = self.result.error is None and not self.result.aborted
        self._retire_event()
        self.refresh_portals("closed")
        return self.result

    def _retire_event(self) -> None:
        """Mark the contract done so a later `brnrd up` doesn't re-wake on it."""
        try:
            event = protocol._read_event(self.event_path)
            if event:
                protocol.set_status(event, "done")
        except Exception:  # noqa: BLE001 — a stale pending event is survivable
            pass


def run_init_wake(
    repo_root: Path,
    runner_name: str,
    **kwargs: Any,
) -> InitWakeResult:
    """Dispatch one init wake and service its portals until it finishes."""
    return _Session(repo_root, runner_name, **kwargs).run()


def collect_facts(
    repo_root: Path,
    *,
    runner_name: str,
    detected_runners: list[str] | None = None,
    detected_shells: list[str] | None = None,
    knowledge_shape: str | None = None,
) -> dict[str, Any]:
    """Everything the wake would otherwise have to shell out for.

    Cheap and best-effort by construction: a fact that can't be read is
    omitted, never guessed, because the wake treats this block as ground
    truth and a confident wrong fact costs an interview beat to unwind.
    """
    from . import home_link
    from .gates import runtime as gate_runtime

    facts: dict[str, Any] = {
        "repo_root": str(repo_root),
        "runner_name": runner_name,
        "detected_runners": list(detected_runners or []),
        "detected_shells": list(detected_shells or []),
        "agents_md": (repo_root / "AGENTS.md").exists(),
    }
    try:
        diagnosis = runner_mod.diagnose_runners(repo_root)
        facts["missing_shells"] = diagnosis.shells_missing
    except Exception:  # noqa: BLE001
        pass
    try:
        facts["configured_gates"] = gate_runtime.configured_gates(
            gitops.shared_brr_dir(repo_root)
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        facts["gh_available"] = home_link.gh_available()
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess

        out = subprocess.run(
            ["git", "remote", "-v"], cwd=repo_root, capture_output=True,
            text=True, check=False, timeout=10,
        )
        remotes = sorted({
            line.split()[1] for line in out.stdout.splitlines() if len(line.split()) > 1
        })
        facts["git_remotes"] = remotes
    except Exception:  # noqa: BLE001
        pass
    if knowledge_shape:
        facts["knowledge_shape"] = knowledge_shape
    return facts


def wake_path_available(repo_root: Path, *, interactive: bool) -> tuple[bool, str]:
    """Whether ``brnrd init`` can hand this session to a wake.

    Returns ``(available, reason)`` — the reason is *printed* when it isn't,
    because "brnrd silently did the boring thing" is the failure this whole
    issue exists to remove. Degradation, not a mode: there is no flag to
    request either path (maintainer decision, 2026-07-22), so the only
    honest surface is a line naming what was skipped and why.
    """
    skipped = "the interview was skipped; running the mechanical install"
    if not interactive:
        return False, f"no TTY on stdin — {skipped}"
    if not prompts.init_playbook_available(repo_root):
        return False, f"the init playbook prompt is not installed — {skipped}"
    if os.environ.get("BRR_NO_INIT_WAKE"):
        # Env, not argv: an escape hatch for CI images and the test suite,
        # deliberately not a documented user choice.
        return False, f"BRR_NO_INIT_WAKE is set — {skipped}"
    return True, ""
