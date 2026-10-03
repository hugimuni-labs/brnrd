"""A runnable vertical slice of the replacement daemon.

The runtime owns coordination, while existing organs own Shell invocation,
prompt assembly, event files, outbox parsing and portal file writing. No
caller imports this package until the integration switch.
"""

from __future__ import annotations

import os
import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import await_verb, prompts, runner
from .authority import SignalAuthority
from .doors import FileDoor, public_event
from .facts import FactStore
from .leases import Lease, LocalLeaseAuthority
from .letters import Claim, LetterService
from .router import Router, UnaddressedLetter
from .seat import Seat, SeatStore, Signal, WakePredicate, legacy_wake_on
from .supervisor import Supervisor


@dataclass(frozen=True)
class RunResult:
    event_id: str
    run_id: str
    returncode: int
    answered: bool
    outbox: Path
    response: Path


class Daemon2:
    def __init__(self, repo_root: Path, home: Path, *,
                 runtime_dir: Path | None = None, runner_name: str | None = None,
                 runner_config: dict[str, Any] | None = None,
                 tick_seconds: float = 0.1):
        self.repo_root = Path(repo_root).resolve()
        self.home = Path(home).resolve()
        self.runtime_dir = Path(runtime_dir or self.repo_root / ".brr").resolve()
        self.runner_name = runner_name
        self.runner_config = runner_config or {}
        self.tick_seconds = tick_seconds
        self.door = FileDoor(self.home / "dispatch" / "inbox",
                             self.home / "dispatch" / "responses")
        self.facts = FactStore(self.home / "daemon2" / "facts")
        self.leases = LocalLeaseAuthority(self.home / "daemon2" / "leases",
                                          facts=self.facts)
        self.seats = SeatStore(self.home / "daemon2" / "seats")
        self.authority = SignalAuthority(self.facts, self.seats)
        self.router = Router(event_lookup=self.door.get)
        self.letters = LetterService(self.facts, self.leases)
        self.supervisor = Supervisor(self.facts)
        self._tick_lock = threading.Lock()

    def _runner_for(self, event: dict[str, Any]) -> runner.RunnerProfile:
        # A pinned command is the operator/test override path; it has no
        # catalog Core. Normal dispatch resolves afresh for every letter.
        if self.runner_config.get("runner_cmd"):
            return runner.runner_profile(self.runner_name or "custom", self.repo_root)
        overrides: dict[str, Any] = {}
        requested = (event.get("dashboard_wake_request_profile")
                     or event.get("dashboard_wake_sticky_profile")
                     or event.get("runner") or self.runner_name)
        if requested:
            overrides["runner"] = requested
        for key in ("shell", "core"):
            if event.get(key):
                overrides[key] = event[key]
        return runner.resolve_runner_profile(self.repo_root, overrides or None)

    def _run_id(self) -> str:
        return "run-" + time.strftime("%y%m%d-%H%M", time.gmtime()) + "-" + uuid.uuid4().hex[:4]

    def _visible(self, conversation: str, current_event: str) -> list[dict[str, Any]]:
        visible = []
        for event in self.door.pending():
            if event["id"] == current_event:
                continue
            try:
                if self.router.route(event).conversation == conversation:
                    visible.append(event)
            except UnaddressedLetter:
                continue
        return visible

    def _notice(self, state: dict[str, Any], text: str, *, kind: str = "refused",
                source_file: str | None = None, verb: str | None = None) -> None:
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "kind": kind, "text": text, "lifetime": "run",
               "run": state["run_id"], "verb": verb or "event"}
        if source_file:
            row["source_file"] = source_file
        state["notices"].append(row)

    def _reply(self, state: dict[str, Any], target_id: str, body: str) -> None:
        if not body:
            raise ValueError("empty reply")
        event = self.door.get(target_id)
        if event is None or event.get("status") not in {"pending", "processing"}:
            raise ValueError(f"event {target_id} is not pending")
        if self.router.route(event).conversation != state["conversation"]:
            raise ValueError("reply target belongs to another conversation")
        claim = state["claim"] if target_id == state["event"]["id"] else None
        if claim is None:
            self.letters.ingest(target_id, str(event["status"]), metadata={
                "conversation": state["conversation"],
                "trust_tier": event.get("trust_tier"),
            })
            claim = self.letters.claim(target_id, state["run_id"], 60, now=time.time())
        if claim is None:
            raise ValueError("reply target is already claimed")
        self.letters.answer(
            claim, body,
            send=lambda key, gen: self.door.send(event, body, key, gen))
        if target_id == state["event"]["id"]:
            state["answered"] = True

    def _handle_outbox(self, state: dict[str, Any], path: Path) -> None:
        verb, fm, body = self.door.parse_outbox(path)
        event_id = state["event"]["id"]
        seat: Seat = state["seat"]
        if verb == "event":
            self._reply(state, str(fm.get("event") or event_id), body)
        elif verb == "note":
            target = str(fm["note"]).strip()
            event = self.door.get(target)
            if event is None or self.router.route(event).conversation != state["conversation"]:
                raise ValueError("note target does not belong to this conversation")
            claim = state["claim"] if target == event_id else self.letters.claim(
                target, state["run_id"], 60, now=time.time())
            if claim is None:
                raise ValueError("note target is already claimed")
            self.letters.retire(claim, by=state["run_id"], why="noted")
            from .. import protocol
            protocol.set_status(event, "noted")
            if target == event_id:
                state["answered"] = True
        elif verb == "await":
            file_path, seconds, error = await_verb.parse_await(fm)
            if error:
                raise ValueError(error)
            if seat.read().state != "running":
                raise ValueError("await needs a running seat")
            armed = seat.await_signal(seat.read().generation, (WakePredicate("M"),))
            state["await"] = {
                "armed": True, "resolved": False,
                "generation": uuid.uuid4().hex,
                "file": file_path,
                "deadline": time.time() + seconds if seconds else None,
                "timeout_seconds": seconds,
                "seat_generation": armed.generation,
            }
        elif verb == "spawn":
            ask = str(fm.get("item") or state["ask"] or "").strip()
            if not ask:
                raise ValueError("spawn needs an ask address")
            edge = uuid.uuid4().hex[:12]
            child_run = self._run_id()
            self.supervisor.register(ask, state["conversation"], state["run_id"],
                                     edge, child_run)
            from .. import protocol
            protocol.create_event(
                self.door.inbox, "spawn", body,
                conversation_key=state["conversation"], ask_id=ask,
                parent_run_id=state["run_id"], spawn_edge=edge,
                child_run_id=child_run,
                branch=str(fm.get("branch") or ""),
                report=str(fm.get("report") or ""),
            )
        elif verb == "submit":
            if not state.get("parent") or not state.get("edge"):
                raise ValueError("submit belongs to a strand")
            self.supervisor.returned(
                state["ask"], state["conversation"], state["parent"],
                state["edge"], state["run_id"],
                report=str(state["event"].get("report") or "") or None,
                branch=str(state["event"].get("branch") or "") or None)
        elif verb == "stop":
            target = str(fm.get("stop") or "")
            child = self.supervisor.children(state["ask"]).get(target)
            if child is None or child.parent != state["run_id"]:
                raise ValueError("stop target is not an owned child")
            self.facts.record("asks", state["ask"], "child_stopped",
                              state["run_id"], {"edge": target, "run": child.run})
        else:
            raise ValueError(f"daemon2 wire verb not yet implemented: {verb}")

    def _tick(self, state: dict[str, Any]) -> None:
        with self._tick_lock:
            outbox_dir: Path = state["outbox"]
            for path in self.door.outbox_entries(outbox_dir):
                try:
                    self._handle_outbox(state, path)
                except Exception as exc:
                    self._notice(state, f"{path.name}: {exc}", source_file=path.name)
                finally:
                    path.unlink(missing_ok=True)
            wait = state.get("await")
            if wait and wait["armed"] and not wait["resolved"]:
                pending = self._visible(state["conversation"], state["event"]["id"])
                outcome, which = await_verb.evaluate(wait["file"], pending)
                if outcome is None and wait["deadline"] is not None and time.time() >= wait["deadline"]:
                    outcome = "timeout"
                if outcome:
                    wait.update(resolved=True, outcome=outcome, which=which)
                    state["seat"].resolve_await(
                        state["seat"].read().generation, outcome)
            visible = self._visible(state["conversation"], state["event"]["id"])
            self.door.write_views(outbox_dir, state["event"]["id"], visible,
                                  phase="awaiting" if wait and wait["armed"]
                                        and not wait["resolved"] else "running",
                                  notices=state["notices"], await_state=wait,
                                  run_id=state["run_id"], repo=str(self.repo_root),
                                  runner_name=state["runner_name"],
                                  branch=str(state["event"].get("branch") or ""),
                                  current_replyable=not state["answered"])

    def once(self) -> RunResult | None:
        events = self.door.pending()
        if not events:
            return None
        event = events[0]
        address = self.router.route(event)
        runner_choice = self._runner_for(event)
        selected_runner = runner_choice.name
        machine = f"{socket.gethostname()}:{os.getpid()}"
        self_lease = self.leases.acquire("self", machine, 60)
        if self_lease is None:
            return None
        run_id = str(event.get("child_run_id") or self._run_id())
        try:
            self.letters.ingest(str(event["id"]), str(event["status"]), metadata={
                "conversation": address.conversation,
                "trust_tier": event.get("trust_tier"),
                "ask": address.ask,
            })
            claim = self.letters.claim(str(event["id"]), run_id, 60,
                                       now=time.time())
            if claim is None:
                return None
            from .. import protocol
            if not self.leases.authorize(claim.lease):
                return None
            protocol.set_status(event, "processing")
            seat = Seat(self.seats, address.conversation,
                        authorize=self.authority.allowed)
            record = seat.read()
            resumed = None
            if record.state == "parked":
                if record.wake_on:
                    resumed = seat.wake(record.generation,
                                        Signal("mail", address.conversation,
                                               ask=address.ask, letter_id=event["id"]),
                                        shell=selected_runner, capabilities=set(),
                                        run_id=run_id)
                    if resumed is None:
                        return None
                else:
                    seat.start(record.generation, run_id=run_id)
            elif record.state in {"running", "awaiting"}:
                resumed = seat.recover(record.generation, shell=selected_runner,
                                       capabilities=set(), run_id=run_id)
            elif record.state == "ended":
                raise ValueError("ended conversation needs a new seat identity")
            outbox = self.runtime_dir / "outbox" / str(event["id"])
            outbox.mkdir(parents=True, exist_ok=True)
            run_dir = self.runtime_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            response = protocol.response_path(self.door.responses, str(event["id"]))
            context = run_dir / "context.md"
            task_text = str(event.get("body") or "")
            if resumed is not None and resumed.mode == "checkpoint":
                recovered = seat.read()
                task_text += ("\n\nRecovery checkpoint (previous Shell stopped):\n"
                              + json.dumps({
                                  "checkpoint": recovered.checkpoint,
                                  "carry": recovered.carry,
                                  "obligations": recovered.obligations,
                                  "queued_letters": recovered.queued_letters,
                              }, sort_keys=True))
            prompt = prompts.build_daemon_prompt(
                task_text, str(event["id"]), str(response),
                self.repo_root, execution_root=self.repo_root,
                outbox_path=str(outbox), run_id=run_id,
                source=str(event.get("source") or ""), environment="host",
                runtime_dir=str(self.runtime_dir), context_path=str(context),
                pending_events=[public_event(e) for e in events],
                event_body=str(event.get("body") or ""),
                event_meta=public_event(event), runner_name=selected_runner,
            )
            context.write_text(prompt, encoding="utf-8")
            state: dict[str, Any] = {
                "event": event, "conversation": address.conversation,
                "ask": address.ask, "parent": address.parent, "edge": address.edge,
                "claim": claim, "seat": seat, "run_id": run_id, "outbox": outbox,
                "await": None, "notices": [], "answered": False,
                "runner_name": selected_runner,
            }
            self._tick(state)
            done = threading.Event()
            def pump() -> None:
                while not done.wait(self.tick_seconds):
                    renewed = self.leases.renew(self_lease, 60)
                    if renewed is None:
                        self._notice(state, "self lease lapsed during runner invocation")
                        break
                    self._tick(state)
            thread = threading.Thread(target=pump, daemon=True)
            thread.start()
            try:
                invocation = runner.RunnerInvocation(
                    kind="daemon", label=run_id, prompt=prompt,
                    repo_root=self.repo_root, cwd=self.repo_root,
                    selected_runner=runner_choice,
                    env={"BRR_OUTBOX_DIR": str(outbox),
                         "BRR_PORTAL_STATE": str(outbox / "portal-state.json"),
                         "BRR_CONVERSATION_ID": address.conversation,
                         "BRR_EVENT_ID": str(event["id"]),
                         "BRR_RUN_ID": run_id},
                    resume_native_session_id=(
                        resumed.session_id if resumed and resumed.mode == "native"
                        else None),
                )
                result = runner.invoke_runner(
                    runner_choice, invocation, self.runner_config)
            finally:
                done.set()
                thread.join(timeout=5)
            self._tick(state)
            if result.stdout.strip() and not state["answered"]:
                self._reply(state, str(event["id"]), result.stdout.strip())
            record = seat.read()
            if record.state == "awaiting":
                seat.park(record.generation, why="turn_ended",
                          wake_on=legacy_wake_on("any"))
            elif record.state == "running":
                seat.checkpoint(record.generation,
                                data={"run": run_id, "last_event": event["id"]},
                                obligations=(() if state["answered"]
                                             else (f"letter:{event['id']}",)),
                                native_session=None)
                record = seat.read()
                seat.park(record.generation, why="turn_ended",
                          wake_on=legacy_wake_on("any"))
            return RunResult(str(event["id"]), run_id, result.returncode,
                             state["answered"], outbox, response)
        finally:
            self.leases.release(self_lease)
