"""The one machine-wide resident seat, persisted under the account home."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import tick as tick_mod


STATES = ("awake", "listening", "parked", "handing-off", "released")

_EDGES = frozenset({
    ("awake", "listening"),
    ("listening", "awake"),
    ("awake", "parked"),
    ("listening", "parked"),
    ("parked", "awake"),
    ("parked", "released"),
    ("awake", "handing-off"),
    ("listening", "handing-off"),
    ("handing-off", "awake"),
    ("awake", "released"),
    ("listening", "released"),
    ("released", "awake"),
})
_MAX_TRANSITIONS = 200
_REGISTRY_PATH = Path("account/repos.json")
_SHUTTLE_PATH = Path("shuttle.json")


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def key_for(account_home: Path) -> str:
    """Return the account identity recorded for *account_home*.

    Project-local homes predate accounts and carry only ``home_id``; retaining
    that as a fallback gives those installations one stable Shuttle too.
    """
    try:
        raw = json.loads((account_home / _REGISTRY_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    key = str(raw.get("account_id") or raw.get("home_id") or "").strip()
    if key:
        return key
    try:
        return str(account_home.resolve())
    except OSError:
        return str(account_home.absolute())


@dataclass
class Shuttle:
    key: str
    state: str
    why: str
    run_id: str
    repo_root: str
    conversation_key: str
    since: str
    transitions: list[dict[str, Any]]
    _account_home: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, account_home: Path) -> "Shuttle":
        path = account_home / _SHUTTLE_PATH
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            shuttle = cls(
                key=key_for(account_home),
                state="released",
                why="first_read",
                run_id="",
                repo_root="",
                conversation_key="",
                since=_stamp(),
                transitions=[],
                _account_home=account_home,
            )
            shuttle.save(account_home)
            return shuttle
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Shuttle record at {path}: {exc}") from exc
        state = str(raw.get("state") or "")
        if state not in STATES:
            raise ValueError(f"invalid Shuttle state {state!r}; expected one of {STATES}")
        return cls(
            key=str(raw.get("key") or key_for(account_home)),
            state=state,
            why=str(raw.get("why") or ""),
            run_id=str(raw.get("run_id") or ""),
            repo_root=str(raw.get("repo_root") or ""),
            conversation_key=str(raw.get("conversation_key") or ""),
            since=str(raw.get("since") or ""),
            transitions=[dict(row) for row in raw.get("transitions") or []],
            _account_home=account_home,
        )

    def save(self, account_home: Path | None = None) -> None:
        home = account_home or self._account_home
        if home is None:
            raise ValueError("Shuttle.save requires an account home")
        home.mkdir(parents=True, exist_ok=True)
        self._account_home = home
        payload = {
            "key": self.key,
            "state": self.state,
            "why": self.why,
            "run_id": self.run_id,
            "repo_root": self.repo_root,
            "conversation_key": self.conversation_key,
            "since": self.since,
            "transitions": self.transitions[-_MAX_TRANSITIONS:],
        }
        fd, tmp_name = tempfile.mkstemp(prefix=".shuttle-", suffix=".tmp", dir=home)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, home / _SHUTTLE_PATH)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def transition(
        self,
        to: str,
        *,
        why: str,
        by: str | None = None,
        run_id: str | None = None,
        repo_root: str | None = None,
        conversation_key: str | None = None,
        tick: int | None = None,
    ) -> None:
        """Move along one edge and persist; the row names the frame's tick.

        *tick* is the caller's beat. Omitted ⇒ ``tick.current`` for this
        Shuttle's home — the loop's latest, ``None`` when no loop has ticked.
        """
        edge = (self.state, to)
        if edge not in _EDGES:
            raise ValueError(f"invalid Shuttle transition {edge[0]} -> {edge[1]}")
        at = _stamp()
        self.transitions.append({
            "at": at,
            "from": self.state,
            "to": to,
            "why": why,
            "by": by or "",
            "tick": tick if tick is not None else tick_mod.current_n(self._account_home),
        })
        self.transitions = self.transitions[-_MAX_TRANSITIONS:]
        self.state = to
        self.why = why
        self.since = at
        if run_id is not None:
            self.run_id = run_id
        if repo_root is not None:
            self.repo_root = repo_root
        if conversation_key is not None:
            self.conversation_key = conversation_key
        self.save()

