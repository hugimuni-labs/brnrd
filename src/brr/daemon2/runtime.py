"""A runnable vertical slice of the replacement daemon.

The runtime owns coordination, while existing organs own Shell invocation,
prompt assembly, event files, outbox parsing and portal file writing. No
caller imports this package until the integration switch.
"""

from __future__ import annotations

import os
import json
import re
import signal
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import account, allowance, await_verb, closekeyword, config as conf, conversations, course, cut_verb, gates, gitops, halt_verb, halts, hold_verb, message_store, promises, prompts, protocol, relics, run_ledger, runner, trust, worktree
from .authority import SignalAuthority
from .doors import FileDoor, public_event
from .facts import FactStore
from .leases import Lease, LocalLeaseAuthority
from .letters import Claim, LetterService
from . import placement as _placement
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
                 tick_seconds: float = 0.1,
                 lease_ttl_seconds: float | None = None,
                 worktree_env: bool = True):
        self.repo_root = Path(repo_root).resolve()
        self.home = Path(home).resolve()
        self.runtime_dir = Path(runtime_dir or self.repo_root / ".brr").resolve()
        self.runner_name = runner_name
        self.runner_config = runner_config or {}
        self.tick_seconds = tick_seconds
        configured_ttl = conf.load_config(self.repo_root).get(
            "daemon2.lease_ttl_seconds", 60)
        self.lease_ttl_seconds = float(
            configured_ttl if lease_ttl_seconds is None else lease_ttl_seconds)
        if self.lease_ttl_seconds <= 0:
            raise ValueError("daemon2 lease ttl must be positive")
        self.worktree_env = worktree_env
        self.door = FileDoor(self.home / "dispatch" / "inbox",
                             self.home / "dispatch" / "responses")
        self.facts = FactStore(self.home / "daemon2" / "facts")
        self.leases = LocalLeaseAuthority(self.home / "daemon2" / "leases",
                                          facts=self.facts)
        self.seats = SeatStore(self.home / "daemon2" / "seats")
        self.authority = SignalAuthority(self.facts, self.seats)
        self.router = Router(event_lookup=self.door.get)
        self.letters = LetterService(self.facts, self.leases)
        self.door.letters = self.letters
        self.supervisor = Supervisor(self.facts)
        self._tick_lock = threading.Lock()
        try:
            self._account_ctx = account.resolve_context(self.repo_root,
                                                        conf.load_config(self.repo_root))
        except Exception:
            self._account_ctx = None

    @staticmethod
    def _terminate_runner(run_id: str, *, grace: float = 0.5) -> None:
        pid = runner.live_pid_for_label(run_id)
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if runner.live_pid_for_label(run_id) is None:
                return
            time.sleep(0.02)
        runner.kill_matching(run_id)

    def _track_claim(self, state: dict[str, Any], claim: Claim) -> Claim:
        with state["claim_lock"]:
            state["claims"][claim.letter] = claim
            if claim.letter == state["event"]["id"]:
                state["claim"] = claim
        return claim

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

    def _visible(self, conversation: str, current_event: str, *,
                 is_child: bool = False, run_id: str = "") -> list[dict[str, Any]]:
        visible = []
        for event in self.door.pending():
            if event["id"] == current_event:
                continue
            if is_child:
                if (event.get("source") != "dispatch_message"
                        or event.get("spawn_message_for_run") != run_id):
                    continue
            elif (event.get("source") == "spawn"
                  or (event.get("source") == "dispatch_message"
                      and event.get("spawn_message_for_run"))):
                continue
            try:
                if self.router.route_or_triage(event).conversation == conversation:
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
        if self.router.route_or_triage(event).conversation != state["conversation"]:
            raise ValueError("reply target belongs to another conversation")
        claim = state["claim"] if target_id == state["event"]["id"] else None
        if claim is None:
            self.letters.ingest(target_id, str(event["status"]), metadata={
                "conversation": state["conversation"],
                "trust_tier": event.get("trust_tier"),
            })
            claim = self.letters.claim(
                target_id, state["run_id"], self.lease_ttl_seconds, now=time.time())
        if claim is None:
            raise ValueError("reply target is already claimed")
        self._track_claim(state, claim)
        with state["claim_lock"]:
            self.letters.answer(
                claim, body,
                send=lambda key, gen: self.door.send(event, body, key, gen))
            state["claims"].pop(target_id, None)
        if target_id == state["event"]["id"]:
            state["answered"] = True

    def _halt_open_items(self, state: dict[str, Any]) -> list[halt_verb.OpenItem]:
        """Attest the open mail, course, produce and children before ending."""
        items: list[halt_verb.OpenItem] = []
        for event in self._visible(state["conversation"], state["event"]["id"],
                                   is_child=False):
            eid = str(event["id"])
            tail = eid.rsplit("-", 1)[-1]
            items.append(halt_verb.OpenItem(
                "event", f"evt-…{tail}", f"evt-…{tail} is pending and unanswered",
                (eid, tail)))
        card = state["outbox"] / ".card"
        parsed = course.parse(card.read_text(encoding="utf-8") if card.exists() else "")
        if parsed is not None:
            for index, row in enumerate(parsed.rows, start=1):
                if not row.done:
                    items.append(halt_verb.OpenItem(
                        "course", f"course:{index}",
                        f"course:{index} unticked — [ ] {row.text}", (row.text,)))
        try:
            branch = str(state["event"].get("branch") or "") or None
            seed = str(state["event"].get("seed_ref") or "") or None
            produce = relics.collect(
                self.repo_root, branch=branch, seed_ref=seed,
                outbox_dir=state["outbox"], commit_run_id=state["run_id"])
            counts = relics.counts_by_kind(produce)
            commits = int(counts.get("commit") or 0)
            if commits and not counts.get("pr"):
                handle = branch or state["run_id"]
                items.append(halt_verb.OpenItem(
                    "produce", handle,
                    f"produce: {commits} commit(s) on {handle} with no PR",
                    (state["run_id"],)))
        except Exception:
            pass  # A missing git scope cannot prove an open item.
        if state["ask"]:
            for child in self.supervisor.children(state["ask"]).values():
                if child.parent == state["run_id"] and child.status == "running":
                    items.append(halt_verb.OpenItem(
                        "strand", child.run,
                        f"strand {child.run} is live — its return needs a successor",
                        (child.run.rsplit("-", 1)[-1],)))
        return items

    def _cut_mismatches(self, state: dict[str, Any],
                        declaration: cut_verb.CutDeclaration) -> list[str]:
        """Check a bolt against facts the daemon can observe at this drain."""
        mismatches: list[str] = []
        declared: dict[str, cut_verb.AskDisposition] = {}
        for row in declaration.asks:
            declared.setdefault(row.event, row)
            declared.setdefault(row.event.rsplit("-", 1)[-1], row)
        for event in self._visible(state["conversation"], state["event"]["id"],
                                   is_child=state["is_child"],
                                   run_id=state["run_id"]):
            eid = str(event["id"])
            tail = eid.rsplit("-", 1)[-1]
            short = f"evt-…{tail}"
            disposition = declared.get(eid) or declared.get(tail)
            if disposition is None:
                mismatches.append(f"{short} undispositioned")
            elif disposition.disposition == "answered":
                mismatches.append(f"{short} declared answered but is still pending")

        reported = relics.read_reported(state["outbox"])
        if not run_ledger.read_run_topics_control(state["outbox"]):
            if not any(row.get("kind") == "item" for row in reported):
                mismatches.append(
                    "topicless: no topic claimed and no item taken — write "
                    ".topics or take an item")
        produce: list[dict[str, Any]] | None = None
        try:
            produce = relics.collect(
                self.repo_root,
                branch=str(state["event"].get("branch") or "") or None,
                seed_ref=str(state["event"].get("seed_ref") or "") or None,
                outbox_dir=state["outbox"], commit_run_id=state["run_id"])
        except Exception:
            pass  # Unavailable git scope is unknown, not a failed attestation.
        if produce is not None:
            if declaration.produce == "none" and produce:
                counts = relics.counts_by_kind(produce)
                phrase = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
                mismatches.append(f"produce: none declared but {phrase} exist")
            elif declaration.produce == "attested" and not produce:
                mismatches.append(
                    "produce: attested declared but the manifest is empty and "
                    "nothing auto-derived")
        shipped = relics.counts_by_kind(produce) if produce is not None else {}
        plan = promises.blueprint(promises.read(state["outbox"]), shipped)
        refs = [row.ref.strip().lower() for row in declaration.owed if row.ref]
        for what, count in sorted(plan.owed.items()):
            labels = plan.labels.get(what) or []
            if not labels and not declaration.owed:
                mismatches.append(f"owed {count} {what}(s) with no carried row")
            for label in labels:
                norm = label.strip().lower()
                if not any(norm in ref or ref in norm for ref in refs):
                    mismatches.append(f"owed: {label!r} has no carried row naming it")
        children = (self.supervisor.children(state["ask"]).values()
                    if state["ask"] else ())
        named_children = {row.run for row in declaration.strands}
        for child in children:
            if (child.parent == state["run_id"] and child.status == "running"
                    and child.run not in named_children):
                mismatches.append(f"strands: {child.run} is live and undispositioned")
        return mismatches

    def _thread_target(self, key: str) -> tuple[dict[str, Any] | None, str | None]:
        """Resolve the latest recorded inbound, including a closed letter."""
        if not key:
            return None, "unknown conversation"
        incoming = []
        for path in self.door.inbox.glob("*.md"):
            event = protocol._read_event(path)
            if (event is not None
                    and event.get("source") not in protocol.INTERNAL_SOURCES
                    and conversations.conversation_key_for_event(event) == key):
                incoming.append(event)
        if not incoming:
            return None, "unknown conversation"
        latest = max(incoming, key=lambda event: str(event["id"]))
        if trust.resolve_tier(latest) != trust.OWNER:
            return None, "correspondent is not an account user"
        if (conversations.gate_thread_key(latest) != key
                or (latest.get("source") == "cloud"
                    and not latest.get("cloud_event_id"))):
            return None, "conversation has no usable gate address"
        return latest, None

    def _gate_available(self, gate: str) -> bool:
        source = "github" if gate == "forge" else gate
        if source not in gates.BUILTIN_GATES:
            return False
        try:
            configured = getattr(gates.import_gate(source), "is_configured", None)
            return bool(configured and configured(self.runtime_dir))
        except (ImportError, OSError):
            return False

    def _child_allowance(self, ask: str, child_run: str, edge: str) -> int:
        initial = allowance.DEFAULT_ALLOWANCE_TOKENS
        for path in self.door.inbox.glob("*.md"):
            event = protocol._read_event(path)
            if event and event.get("source") == "spawn" and event.get("child_run_id") == child_run:
                initial = int(event.get("allowance_tokens") or initial)
                break
        for fact in self.facts.read("asks", ask):
            if fact.kind == "allowance_granted" and fact.data.get("edge") == edge:
                initial = int(fact.data["total"])
        return initial

    def _handle_outbox(self, state: dict[str, Any], path: Path) -> None:
        verb, fm, body = self.door.parse_outbox(path)
        event_id = state["event"]["id"]
        seat: Seat = state["seat"]
        if verb == "event":
            target = str(fm.get("event") or event_id)
            primary = self.door.get(target)
            # Validate and claim the entire burst before any outward send.
            # A partial reply would claim to have handled mail it did not.
            also_raw = str(fm.get("also") or "").strip()
            siblings: list[tuple[dict[str, Any], Claim]] = []
            seen = {target}
            try:
                for also_id in (s.strip() for s in also_raw.split(",") if s.strip()):
                    if also_id in seen:
                        continue
                    seen.add(also_id)
                    also_event = self.door.get(also_id)
                    if also_event is None:
                        raise ValueError(
                            f"also dropped: event {also_id} not found in any inbox "
                            "(the id is wrong, or the event is gone) — nothing was delivered")
                    if also_event.get("status") != "pending":
                        raise ValueError(f"also dropped: event {also_id} is not pending — nothing was delivered")
                    if (primary is None
                            or self.router.route_or_triage(also_event).conversation
                            != self.router.route_or_triage(primary).conversation
                            or not conversations.correspondent_key_for_event(primary)
                            or conversations.correspondent_key_for_event(also_event)
                            != conversations.correspondent_key_for_event(primary)):
                        raise ValueError(f"also dropped: event {also_id} is not the same thread/correspondent as the event: target — nothing was delivered")
                    if state["is_child"] and also_id != event_id:
                        raise ValueError(f"also refused: event {also_id} belongs to another thread — nothing was delivered")
                    self.letters.ingest(also_id, str(also_event["status"]))
                    also_claim = self.letters.claim(also_id, state["run_id"],
                                                    self.lease_ttl_seconds, now=time.time())
                    if also_claim is None:
                        raise ValueError(f"also dropped: event {also_id} could not be claimed — nothing was delivered")
                    self._track_claim(state, also_claim)
                    siblings.append((also_event, also_claim))
                self._reply(state, target, body)
            except Exception:
                for _, held in siblings:
                    self.letters.release(held, why="burst_refused")
                    with state["claim_lock"]:
                        state["claims"].pop(held.letter, None)
                raise
            for also_event, also_claim in siblings:
                with state["claim_lock"]:
                    self.letters.retire(also_claim, by=state["run_id"], why="also")
                    state["claims"].pop(also_claim.letter, None)
        elif verb == "note":
            target = str(fm["note"]).strip()
            pending = self.door.pending()
            event = next((row for row in pending if row["id"] == target), None)
            if event is None:
                tail = target.rsplit("-", 1)[-1]
                matches = [row for row in pending
                           if row["id"].rsplit("-", 1)[-1] == tail]
                if len(matches) == 1:
                    event = matches[0]
                elif len(matches) > 1:
                    self._notice(state, f"note dropped: event {target} is ambiguous — "
                                 f"matches {len(matches)} pending events; address the "
                                 "full id — nothing was retired",
                                 source_file=path.name, verb="note")
                    return
            if event is None:
                located = self.door.get(target)
                cause = (f"event {target} found in {self.door.inbox}, but "
                         f"status={located['status']} (not pending)" if located else
                         f"event {target} not found in any inbox "
                         "(the id is wrong, or the event is gone)")
                self._notice(state, f"note dropped: {cause} — nothing was retired",
                             source_file=path.name, verb="note")
                return
            target = str(event["id"])
            if self.router.route_or_triage(event).conversation != state["conversation"]:
                raise ValueError("note target does not belong to this conversation")
            claim = state["claim"] if target == event_id else self.letters.claim(
                target, state["run_id"], self.lease_ttl_seconds, now=time.time())
            if claim is None:
                raise ValueError("note target is already claimed")
            self._track_claim(state, claim)
            with state["claim_lock"]:
                self.letters.retire(claim, by=state["run_id"], why="noted")
                state["claims"].pop(target, None)
            if target == event_id:
                state["answered"] = True
            if body:
                self._notice(
                    state, f"note: body text ignored — a note closes event {target} "
                    "without speaking; use event: to reply", kind="advisory",
                    source_file=path.name, verb="note")
        elif verb == "hold":
            spec, error = hold_verb.parse_hold(fm)
            if error:
                self._notice(state, f"hold dropped: {error}", kind="dropped",
                             source_file=path.name, verb="hold")
                return
            assert spec is not None
            resume = str(spec["resume_condition"])
            rest = ("`brnrd await` is the resting state — a message, an own strand, "
                    "or a tick resolves it, and the seat stays warm")
            if resume not in {"refill", "reset"}:
                refusal = (f"hold refused: resume: {resume} is not a resource wall — "
                           f"{rest}. `hold:` parks only on a wall the daemon can "
                           "confirm: `resume: refill` (or `reset`) with the binding "
                           "quota measured under the starvation floor")
            else:
                floor = float(conf.load_config(self.repo_root).get(
                    "seat.starve_floor_pct", 2))
                pct = state.get("quota_binding_pct")
                reading = (f"last read {float(pct):.1f}%"
                           if isinstance(pct, (int, float)) else "not measured this run")
                refusal = (f"hold refused: resume: {resume} needs a measured wall — "
                           f"the binding quota is {reading} (floor {floor:g}%) — {rest}")
            self._notice(state, refusal, source_file=path.name, verb="hold")
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
            branch = str(fm.get("branch") or "").strip()
            report = str(fm.get("report") or "").strip()
            if not branch or not report:
                raise ValueError("spawn requires branch and report paths")
            raw_allowance = str(fm.get("allowance") or "").strip()
            tokens = (allowance.parse_tokens(raw_allowance) if raw_allowance
                      else allowance.DEFAULT_ALLOWANCE_TOKENS)
            if tokens is None:
                raise ValueError(f"spawn refused: allowance: {raw_allowance!r} is not a valid token count")
            edge = uuid.uuid4().hex[:12]
            child_run = self._run_id()
            self.supervisor.register(ask, state["conversation"], state["run_id"],
                                     edge, child_run)
            protocol.create_event(
                self.door.inbox, "spawn", body,
                conversation_key=state["conversation"], ask_id=ask,
                parent_run_id=state["run_id"], spawn_edge=edge,
                child_run_id=child_run,
                branch=branch, report=report,
                allowance_tokens=tokens,
                shell=str(fm.get("shell") or ""),
                core=str(fm.get("core") or ""),
            )
        elif verb == "ask":
            if not state["is_child"]:
                self._notice(state, "ask refused: allowance asks are a strand's own verb",
                             source_file=path.name, verb="ask")
                return
            spec = str(fm.get("ask") or "").strip()
            match = re.match(r"^allowance\s+([+-]?[0-9][0-9.]*[km]?)\b(.*)$",
                             spec, re.IGNORECASE)
            delta = allowance.parse_signed_tokens(match.group(1)) if match else None
            if delta is None or delta <= 0:
                self._notice(state,
                             f"ask refused: {spec!r} does not name a positive token "
                             "delta (e.g. `ask: allowance +50k`)",
                             source_file=path.name, verb="ask")
                return
            if not body:
                self._notice(state,
                             "ask refused: `ask: allowance +N` needs a one-line why in the body",
                             source_file=path.name, verb="ask")
                return
            if not state.get("parent") or not state.get("edge"):
                self._notice(state, "ask refused: spawning parent is missing",
                             source_file=path.name, verb="ask")
                return
            current = self._child_allowance(state["ask"], state["run_id"],
                                            state["edge"])
            protocol.create_event(
                self.door.inbox, "spawn_allowance_requested",
                f"{state['run_id']} asks +{allowance.format_tokens(delta)} allowance "
                f"(spent ?/{allowance.format_tokens(current)}): {body}",
                conversation_key=state["conversation"],
                spawned_by_run=state["run_id"],
                spawned_by_event=event_id,
                spawn_parent_run_id=state["parent"],
                spawn_allowance_request_tokens=delta,
                spawn_allowance_tokens=current,
            )
            self.facts.record("asks", state["ask"], "allowance_requested",
                              state["run_id"], {"edge": state["edge"],
                                                "tokens": delta, "why": body})
        elif verb == "submit":
            if not state.get("is_child") or not state.get("parent") or not state.get("edge"):
                raise ValueError("submit belongs to a strand")
            report = str(state["event"].get("report") or "")
            branch = str(state["event"].get("branch") or "")
            if not report or not Path(report).is_file() or not branch:
                raise ValueError("submit requires its declared branch and stat-able report")
            allocation = state.get("allocation")
            if allocation is not None:
                actual = worktree.current_branch(allocation.path)
                if actual != branch:
                    raise ValueError(f"submit requires declared branch {branch!r}; "
                                     f"clone is on {actual!r}")
                landed = _placement.land(self.repo_root, allocation, branch=branch)
                if not landed.success:
                    raise ValueError(f"submit could not land branch: {landed.detail}")
                if gitops.remote_url(self.repo_root, "origin"):
                    pushed = gitops.push_branch(self.repo_root, "origin", branch)
                    if not pushed:
                        raise ValueError(f"submit could not publish branch: {pushed.detail}")
            elif self.worktree_env:
                raise ValueError("submit requires a placed strand worktree")
            self.supervisor.returned(
                state["ask"], state["conversation"], state["parent"],
                state["edge"], state["run_id"],
                report=report, branch=branch)
            generation = self.supervisor.children(state["ask"])[state["edge"]].generation
            protocol.create_event(
                self.door.inbox, "spawn_submitted", body or "Strand submitted",
                conversation_key=state["conversation"], ask_id=state["ask"],
                spawn_parent_run_id=state["parent"],
                spawned_by_event=state["edge"],
                spawned_by_run=state["run_id"],
                spawn_report_path=report,
                spawn_published_branch=branch,
                spawn_submit_generation=generation,
            )
            if not state["answered"]:
                with state["claim_lock"]:
                    self.letters.answer(
                        state["claim"], body or "Strand submitted",
                        send=lambda key, gen: self.door.send(
                            state["event"], body or "Strand submitted", key, gen))
                    state["claims"].pop(state["event"]["id"], None)
                state["answered"] = True
        elif verb == "stop":
            target = str(fm.get("stop") or "").strip()
            if not target:
                self._notice(state, "stop dropped: no target run/event id",
                             kind="dropped", source_file=path.name, verb="stop")
                return
            children = self.supervisor.children(state["ask"]) if state["ask"] else {}
            child = children.get(target) or next(
                (row for row in children.values() if row.run == target), None)
            child_event = next((event for event in (
                protocol._read_event(p) for p in self.door.inbox.glob("*.md"))
                if event and event.get("source") == "spawn"
                and event.get("id") == target), None)
            if child is None and child_event is not None:
                child = children.get(str(child_event.get("spawn_edge") or ""))
            if child is None:
                self._notice(
                    state, f"stop refused: {target!r} matches no live concurrent spawn "
                    "(already finished, never dispatched here, or the id is wrong)",
                    source_file=path.name, verb="stop")
                return
            if child.parent != state["run_id"] or child.conversation != state["conversation"]:
                raise ValueError("stop target is not an owned child")
            if child.status == "stopped":
                raise ValueError("stop target is already stopped")
            if child_event is None:
                child_event = next((event for event in (
                    protocol._read_event(p) for p in self.door.inbox.glob("*.md"))
                    if event and event.get("source") == "spawn"
                    and event.get("child_run_id") == child.run), None)
            self.facts.record("asks", state["ask"], "child_stopped",
                              state["run_id"], {"edge": child.edge, "run": child.run,
                                                "reason": str(fm.get("reason") or body)})
            self._terminate_runner(child.run)
            protocol.create_event(
                self.door.inbox, "spawn_completed",
                f"concurrent spawn {child.run} stopped by {state['run_id']}",
                conversation_key=state["conversation"], ask_id=state["ask"],
                spawn_parent_run_id=state["run_id"], spawned_by_run=child.run,
                spawned_by_event=str(child_event["id"] if child_event else child.edge),
                spawn_stopped=True, spawn_status="stopped",
                spawn_report_path=child.report or "",
                spawn_published_branch=child.branch or "",
            )
        elif verb == "to":
            # to: <edge-or-run-id> — steer a child in this conversation
            # A strand belongs to the conversation that dispatched it; any run
            # in that conversation may steer it, not just the spawning run.
            target = str(fm.get("to") or "").strip()
            if not target:
                raise ValueError("to: requires a child edge or run id")
            children = self.supervisor.children(state["ask"] or "")
            child = (children.get(target)
                     or next((c for c in children.values() if c.run == target), None))
            if child is None or child.conversation != state["conversation"]:
                raise ValueError(f"to: {target!r} is not a child of this conversation")
            if child.status != "running":
                raise ValueError(f"to: child {target!r} is not running (status={child.status!r})")
            first = body.splitlines()[0].strip() if body else ""
            grant = re.fullmatch(r"allowance:\s*([+-]?[0-9][0-9.]*[km]?)",
                                 first, re.IGNORECASE)
            total = None
            if grant:
                raw = grant.group(1)
                value = allowance.parse_signed_tokens(raw)
                if value is not None:
                    current = self._child_allowance(state["ask"], child.run, child.edge)
                    total = max(0, current + value if raw[0] in "+-" else value)
                    self.facts.record("asks", state["ask"], "allowance_granted",
                                      state["run_id"], {"edge": child.edge,
                                                        "run": child.run, "total": total})
            protocol.create_event(
                self.door.inbox, "dispatch_message", body,
                conversation_key=child.conversation,
                spawn_message_for_run=child.run,
                allowance_tokens=total if total is not None else "",
            )
        elif verb in {"halt", "respawn"}:
            # The older respawn spelling is a carried halt: both retire this
            # body, so both must account for its open work before handover.
            if verb == "respawn":
                marker = str(fm.get("respawn") or "").strip().lower()
                if marker not in {"", "true", "1", "yes", "on"}:
                    raise ValueError("respawn: use `respawn: true`")
                if not body:
                    raise ValueError("respawn: a successor needs a carry brief")
                unsupported = sorted(set(fm) - {"respawn", "reason", "shell", "core", "topic"})
                if unsupported:
                    raise ValueError("respawn: unsupported field(s) " + ", ".join(unsupported))
                halt_fm = {"halt": "true", "reason": str(fm.get("reason") or "respawn requested"),
                           "carry": body, "shell": fm.get("shell"), "core": fm.get("core")}
                announcement_body = ""
            else:
                halt_fm = fm
                announcement_body = body
            declaration, error = halt_verb.parse_halt(halt_fm)
            if error:
                raise ValueError(error)
            if state["is_child"]:
                raise ValueError("halt: a strand cannot end the resident seat; use submit: true")
            open_items = self._halt_open_items(state)
            unnamed = halt_verb.unnamed(declaration, open_items)
            signature = (declaration.reason, declaration.carry, declaration.resumable)
            if unnamed and state.get("halt_bounced") != signature:
                state["halt_bounced"] = signature
                field = "carry:" if declaration.carried else "resumable:"
                lines = " · ".join(item.line for item in unnamed)
                raise ValueError(
                    f"halt bounced: {len(unnamed)} open item(s) your {field} does not name "
                    f"— {lines} · name each one in {field} (its handle is enough), "
                    "or stage the halt again unchanged and it stands, annotated with what it left open")
            record = seat.read()
            announcement = announcement_body or (
                f'halt — "{declaration.reason}"\n\n'
                + (f"The work continues; a successor carries this brief:\n{declaration.carry}"
                   if declaration.carry else
                   f"The work stops here. What would pick it back up:\n{declaration.resumable}"))
            successor = ""
            if declaration.carry:
                # Mint before parking: after a crash at any later instruction
                # the brief is still a pending letter, not an unwakeable seat.
                successor = protocol.create_event(
                    self.door.inbox, "respawn", declaration.carry,
                    conversation_key=state["conversation"], ask_id=state["ask"] or "",
                    handover_from_run=state["run_id"],
                    handover_from_generation=record.generation,
                    shell=declaration.shell, core=declaration.core,
                ).stem
            if not state["answered"]:
                self._reply(state, event_id, announcement)
            if declaration.carry:
                handed = seat.handover(
                    record.generation,
                    carry={"text": declaration.carry, "reason": declaration.reason,
                           "shell": declaration.shell, "core": declaration.core},
                    wake_on=(WakePredicate("H"), WakePredicate("M")))
                seat.queue_letter(handed.generation, successor)
            else:
                seat.end(record.generation)
            halts.record(
                self.home, run_id=state["run_id"],
                conversation_key=state["conversation"],
                kind=declaration.kind, reason=declaration.reason,
                carry=declaration.carry, resumable=declaration.resumable,
                successor_event=successor,
                open_items=[item.line for item in open_items])
            state["halted"] = True
        elif verb == "cut":
            declaration, error = cut_verb.parse_cut(fm)
            if error:
                self._notice(state, f"cut dropped: {error}", kind="dropped",
                             source_file=path.name, verb="cut")
                return
            mismatches = self._cut_mismatches(state, declaration)
            if mismatches:
                attempts = int(state.get("cut_bounces") or 0) + 1
                state["cut_bounces"] = attempts
                if attempts < 3:
                    self._notice(state, "cut bounced: " + " · ".join(mismatches),
                                 source_file=path.name, verb="cut")
                    return
                plural = "s" if len(mismatches) != 1 else ""
                body = ((body.rstrip("\n") + "\n\n") if body else "") + (
                    f"---\ndaemon: {len(mismatches)} check{plural} unresolved — "
                    + " · ".join(mismatches))
            self.facts.record(
                "seats", state["conversation"], "cut_accepted", state["run_id"],
                {"run": state["run_id"],
                 "attempts": int(state.get("cut_bounces") or 0)
                 + (0 if mismatches else 1),
                 "declaration": cut_verb.durable_declaration(declaration,
                                                               dissent=mismatches)})
            if body and not state["answered"]:
                self._reply(state, event_id, body)
            record = seat.read()
            if record.state == "running":
                handoffs: list[Any] = []
                if state["ask"]:
                    children = self.supervisor.children(state["ask"])
                    for row in declaration.strands:
                        child = next((c for c in children.values()
                                      if c.run == row.run), None)
                        if (child and child.parent == state["run_id"]
                                and child.status == "running"
                                and row.disposition.lower().startswith("handoff")):
                            handoffs.append(child)
                seat.checkpoint(record.generation,
                                data={"run": state["run_id"],
                                      "last_event": event_id},
                                obligations=tuple(f"strand:{c.run}" for c in handoffs),
                                native_session=None)
                record = seat.read()
                seat.park(
                    record.generation,
                    why="strands" if handoffs else "cut",
                    wake_on=(legacy_wake_on(
                        "strands", parent=state["run_id"],
                        edges=tuple(c.edge for c in handoffs))
                        if handoffs else legacy_wake_on("any")))
            state["cut"] = True
        elif verb == "gate":
            gate_name = str(fm.get("gate") or "").strip()
            if not gate_name:
                raise ValueError("gate: requires a gate name")
            if gate_name == "forge":
                if not self._gate_available(gate_name):
                    self._notice(state, "gate message dropped: 'forge' (resolved to "
                                 "'github') is not deliverable on this account; "
                                 "the message was NOT delivered",
                                 kind="dropped", source_file=path.name, verb="gate")
                    return
                head = str(fm.get("head") or "").strip()
                base = str(fm.get("base") or "").strip()
                title = str(fm.get("title") or "").strip()
                if not all((head, base, title, body)):
                    raise ValueError("gate: forge requires head, base, title and body")
                findings = closekeyword.check(
                    body, channel=closekeyword.PR_BODY.label)
                if findings:
                    self._notice(state, closekeyword.render(
                        findings, channel=closekeyword.PR_BODY.label),
                        source_file=path.name, verb="gate")
                    return
                prior = next((event for event in (
                    protocol._read_event(p) for p in self.door.inbox.glob("*.md"))
                    if event and event.get("source") == "github"
                    and event.get("run_id") == state["run_id"]
                    and event.get("source_ref") == path.name), None)
                if prior is None:
                    synthetic = protocol.create_event(
                        self.door.inbox, "github", "", status="done",
                        github_action="pull_request", head=head, base=base,
                        title=title, run_id=state["run_id"],
                        source_ref=path.name,
                        repo_label=str(state["event"].get("repo_label") or ""))
                    target = synthetic.stem
                else:
                    target = str(prior["id"])
                protocol.write_response(self.door.responses, target, body)
                (state["outbox"] / ".forge-handoff").write_text(
                    f"event: {target}\nhead: {head}\n", encoding="utf-8")
                if self._account_ctx is not None:
                    try:
                        message_store.stage(
                            self._account_ctx,
                            repo_label=str(state["event"].get("repo_label") or ""),
                            run_id=state["run_id"], body=body, kind="outbound",
                            target_gate="forge", source_ref=path.name)
                    except Exception as exc:
                        self._notice(state, f"gate 'forge': carrier queued, but "
                                     f"message record failed: {exc}",
                                     kind="advisory", source_file=path.name,
                                     verb="gate")
                return
            if not body:
                self._notice(state, f"gate message dropped: gate {gate_name!r} "
                             "had no body/inbox", kind="dropped",
                             source_file=path.name, verb="gate")
                return
            if not self._gate_available(gate_name):
                configured = [name for name in gates.BUILTIN_GATES
                              if self._gate_available(name)]
                self._notice(
                    state, f"gate message dropped: {gate_name!r} is not "
                    "deliverable on this account (configured gates: "
                    f"{', '.join(configured) if configured else 'none'}); "
                    "the message was NOT delivered",
                    kind="dropped", source_file=path.name, verb="gate")
                return
            gate_module = gates.import_gate(gate_name)
            if not getattr(gate_module, "CAN_SEND_UNADDRESSED", True):
                addressed = getattr(gate_module, "addressed", None)
                if not addressed or not addressed(fm):
                    self._notice(
                        state, f"gate message dropped: {gate_name!r} cannot "
                        "originate an unaddressed send and this message carries "
                        "no addressing it can use; the message was NOT delivered",
                        source_file=path.name, verb="gate")
                    return
            target_meta = {key: value for key, value in fm.items()
                           if key not in {"gate", "event", "id", "source", "status", "created"}}
            carrier = protocol.create_event(
                self.door.inbox, gate_name, "", status="done",
                run_id=state["run_id"],
                repo_label=str(state["event"].get("repo_label") or ""),
                **target_meta)
            protocol.write_response(self.door.responses, carrier.stem, body)
            if self._account_ctx is not None:
                try:
                    message_store.stage(
                        self._account_ctx,
                        repo_label=str(state["event"].get("repo_label") or ""),
                        run_id=state["run_id"], body=body, kind="outbound",
                        target_gate=gate_name, source_ref=path.name)
                except Exception as exc:
                    self._notice(state, f"gate {gate_name!r}: carrier queued, but "
                                 f"message record failed: {exc}", kind="advisory",
                                 source_file=path.name, verb="gate")
        elif verb == "thread":
            key = str(fm.get("thread") or "").strip()
            event, refusal = self._thread_target(key)
            if refusal:
                self._notice(state, f"thread refused: {refusal} ({key!r})",
                             source_file=path.name, verb="thread")
                return
            if not body:
                self._notice(state, f"thread refused: message has no body ({key!r})",
                             source_file=path.name, verb="thread")
                return
            if self._account_ctx is None:
                raise ValueError("thread: requires an account context")
            message_store.stage(
                self._account_ctx,
                repo_label=self._account_ctx.default_repo.label,
                run_id=state["run_id"],
                body=body,
                kind="outbound",
                target_gate=str(event["source"]),
                target_thread=key,
                source_ref=path.name,
            )
        else:
            raise ValueError(f"daemon2 wire verb not yet implemented: {verb}")

    def _tick(self, state: dict[str, Any]) -> None:
        with self._tick_lock:
            outbox_dir: Path = state["outbox"]
            for path in self.door.outbox_entries(outbox_dir):
                try:
                    self._handle_outbox(state, path)
                except Exception as exc:
                    self._notice(state, str(exc), source_file=path.name)
                finally:
                    path.unlink(missing_ok=True)
            wait = state.get("await")
            if wait and wait["armed"] and not wait["resolved"]:
                pending = self._visible(
                    state["conversation"], state["event"]["id"],
                    is_child=state["is_child"], run_id=state["run_id"])
                outcome, which = await_verb.evaluate(wait["file"], pending)
                if outcome is None and wait["deadline"] is not None and time.time() >= wait["deadline"]:
                    outcome = "timeout"
                if outcome:
                    wait.update(resolved=True, outcome=outcome, which=which)
                    state["seat"].resolve_await(
                        state["seat"].read().generation, outcome)
            visible = self._visible(
                state["conversation"], state["event"]["id"],
                is_child=state["is_child"], run_id=state["run_id"])
            self.door.write_views(outbox_dir, state["event"]["id"], visible,
                                  phase="awaiting" if wait and wait["armed"]
                                        and not wait["resolved"] else "running",
                                  notices=state["notices"], await_state=wait,
                                  run_id=state["run_id"], repo=str(self.repo_root),
                                  runner_name=state["runner_name"],
                                  branch=str(state["event"].get("branch") or ""),
                                  current_replyable=not state["answered"])

    def once(self, *, role: str = "any") -> RunResult | None:
        if role not in {"any", "resident", "strand"}:
            raise ValueError("role must be any, resident or strand")
        events = self.door.pending()
        if not events:
            return None
        machine = f"{socket.gethostname()}:{os.getpid()}"
        selected = None
        for event in events:
            is_child = event.get("source") == "spawn"
            if role == "resident" and is_child or role == "strand" and not is_child:
                continue
            address = self.router.route_or_triage(event)
            runner_choice = self._runner_for(event)
            selected_runner = runner_choice.name
            run_id = str(event.get("child_run_id") or self._run_id())
            execution_key = ("strand:" + str(event["id"])) if is_child else "self"
            execution_lease = self.leases.acquire(
                execution_key, machine, self.lease_ttl_seconds)
            if execution_lease is None:
                continue
            self.letters.ingest(str(event["id"]), str(event["status"]), metadata={
                "conversation": address.conversation,
                "trust_tier": event.get("trust_tier"),
                "ask": address.ask,
            })
            claim = self.letters.claim(str(event["id"]), run_id, self.lease_ttl_seconds,
                                       now=time.time())
            if claim is None:
                self.leases.release(execution_lease)
                continue
            if is_child:
                child = self.supervisor.children(address.ask or "").get(address.edge or "")
                if child is not None and child.status == "stopped":
                    self.letters.retire(claim, by=child.parent, why="stopped_before_start")
                    self.leases.release(execution_lease)
                    continue
            selected = (event, address, runner_choice, selected_runner,
                        run_id, execution_lease, claim, is_child)
            break
        if selected is None:
            return None
        (event, address, runner_choice, selected_runner,
         run_id, execution_lease, claim, is_child) = selected
        held_claims = {str(event["id"]): claim}
        claim_lock = threading.Lock()
        heartbeat_done = threading.Event()
        heartbeat_lost = threading.Event()
        def heartbeat() -> None:
            interval = max(0.01, self.lease_ttl_seconds / 3)
            while not heartbeat_done.wait(interval):
                if is_child:
                    child = self.supervisor.children(address.ask or "").get(address.edge or "")
                    if child is not None and child.status == "stopped":
                        self._terminate_runner(run_id)
                        return
                if self.leases.renew(execution_lease, self.lease_ttl_seconds) is None:
                    heartbeat_lost.set()
                    self._terminate_runner(run_id)
                    return
                with claim_lock:
                    for letter_id, held in list(held_claims.items()):
                        renewed_claim = self.leases.renew(
                            held.lease, self.lease_ttl_seconds)
                        if renewed_claim is None:
                            heartbeat_lost.set()
                            self._terminate_runner(run_id)
                            return
                        fresh = Claim(letter_id, renewed_claim)
                        held_claims[letter_id] = fresh
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            if not self.leases.authorize(claim.lease):
                return None
            seat_address = (f"{address.conversation}#strand:{run_id}"
                            if is_child else address.conversation)
            seat = Seat(self.seats, seat_address,
                        authorize=self.authority.allowed)
            record = seat.read()
            resumed = None
            if record.state == "parked":
                if record.wake_on:
                    from_run = str(event.get("handover_from_run") or "")
                    from_generation = event.get("handover_from_generation")
                    signal = (Signal("handover", seat_address,
                                     from_run=from_run,
                                     from_generation=int(from_generation))
                              if from_run and from_generation is not None else
                              Signal("mail", seat_address, ask=address.ask,
                                     letter_id=event["id"]))
                    resumed = seat.wake(record.generation, signal,
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
                seat.start(record.generation, run_id=run_id)
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
                strand=is_child,
            )
            context.write_text(prompt, encoding="utf-8")
            state: dict[str, Any] = {
                "event": event, "conversation": address.conversation,
                "ask": address.ask, "parent": address.parent, "edge": address.edge,
                "claim": claim, "seat": seat, "run_id": run_id, "outbox": outbox,
                "await": None, "notices": [], "answered": False,
                "runner_name": selected_runner,
                "is_child": is_child,
                "claims": held_claims, "claim_lock": claim_lock,
                "crashed": False, "halted": False, "cut": False,
            }
            if not address.routable:
                self._notice(state, f"unaddressed letter retained on triage seat: {address.reason}",
                             kind="advisory", verb="route")
            self._tick(state)
            done = threading.Event()
            def pump() -> None:
                while not done.wait(self.tick_seconds):
                    if heartbeat_lost.is_set():
                        return
                    self._tick(state)
            thread = threading.Thread(target=pump, daemon=True)
            thread.start()
            try:
                if heartbeat_lost.is_set():
                    return RunResult(str(event["id"]), run_id, 125,
                                     False, outbox, response)
                strand_alloc: _placement.Allocation | None = None
                if is_child and self.worktree_env:
                    try:
                        strand_alloc = _placement.allocate(self.repo_root, run_id)
                    except _placement.PlacementError as exc:
                        self._notice(state, f"worktree allocation failed: {exc}",
                                     kind="advisory")
                state["allocation"] = strand_alloc
                strand_root = (strand_alloc.path
                               if strand_alloc is not None else self.repo_root)
                strand_git_env: dict[str, str] = (strand_alloc.env()
                                                   if strand_alloc is not None else {})
                try:
                    invocation = runner.RunnerInvocation(
                        kind="strand" if is_child else "daemon",
                        label=run_id, prompt=prompt,
                        repo_root=strand_root, cwd=strand_root,
                        selected_runner=runner_choice,
                        env={**strand_git_env,
                             "BRR_OUTBOX_DIR": str(outbox),
                             "BRR_PORTAL_STATE": str(outbox / "portal-state.json"),
                             "BRR_CONVERSATION_ID": address.conversation,
                             "BRR_EVENT_ID": str(event["id"]),
                             "BRR_RUN_ID": run_id,
                             "BRR_IS_STRAND": "1" if is_child else "0",
                             "BRR_SOURCE": str(event.get("source") or ""),
                             "BRR_REPORT_PATH": str(event.get("report") or ""),
                             "BRR_BRANCH": str(event.get("branch") or "")},
                        resume_native_session_id=(
                            resumed.session_id if resumed and resumed.mode == "native"
                            else None),
                    )
                    result = runner.invoke_runner(
                        runner_choice, invocation, self.runner_config)
                finally:
                    if strand_alloc is not None:
                        try:
                            publication = _placement.publish(self.repo_root, strand_alloc)
                            if not publication.landed or not publication.released:
                                self._notice(
                                    state, f"strand clone retained at {strand_alloc.path}: "
                                    f"{publication.detail or 'branch publication incomplete'}",
                                    kind="advisory", verb="placement")
                        except _placement.PlacementError as exc:
                            self._notice(state, f"strand clone retained: {exc}",
                                         kind="advisory", verb="placement")
            finally:
                done.set()
                thread.join(timeout=5)
            if heartbeat_lost.is_set():
                # A lapsed execution/letter lease is the crash path. Leave
                # the seat checkpoint and letter claim for expiry/recovery.
                return RunResult(str(event["id"]), run_id, result.returncode,
                                 False, outbox, response)
            if is_child:
                child = self.supervisor.children(address.ask or "").get(address.edge or "")
                if child is not None and child.status == "stopped":
                    held = held_claims.get(str(event["id"]))
                    if held is not None and self.letters.state(str(event["id"])).state == "claimed":
                        self.letters.retire(held, by=child.parent, why="stopped")
                        held_claims.pop(str(event["id"]), None)
                    seat.end(seat.read().generation)
                    return RunResult(str(event["id"]), run_id, result.returncode,
                                     state["answered"], outbox, response)
            self._tick(state)
            if result.stdout.strip() and not state["answered"]:
                self._reply(state, str(event["id"]), result.stdout.strip())
            record = seat.read()
            if state["halted"] or state["cut"]:
                pass  # seat already transitioned inside _handle_outbox
            elif record.state == "awaiting":
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
            heartbeat_done.set()
            heartbeat_thread.join(timeout=2)
            self.leases.release(execution_lease)
