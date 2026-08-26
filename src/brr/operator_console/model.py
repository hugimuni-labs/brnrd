"""Read-only projections for the local brnrd operator console.

The console deliberately owns no runtime state. It reads the same files and
library projections the daemon, gates, and dashboard already use, then renders
them for a human sitting beside the daemon.

Resident speech is deliberately absent from this first slice. A ``source=cli``
event is not a local terminal route: because no gate owns that source, the
daemon may resolve ``notify.gate`` from recent activity and deliver the reply
to a real chat gate. Input belongs here only after brnrd has an explicit local
operator delivery contract.

The BOOT projection is deliberately stricter than a reconstructed command line.
``prompt.md`` is exact evidence of what brnrd handed across the daemon/runner
boundary; a ``session-start`` row in ``boundaries.jsonl`` is native evidence
that the selected Shell actually started and called back into brnrd. Anything
owned by the runner/model outside those surfaces stays marked opaque rather
than being silently reconstructed from whatever profile happens to be current
when the console is opened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .. import conversations, daemon, gitops, presence, protocol


_CONSOLE_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class Boundary:
    seq: int
    phase: str
    at: str
    act: str
    inject: str
    block: bool = False
    block_reason: str = ""
    detail: str = ""    # redacted tool detail (cmd/path/summary); "" when absent
    out_bytes: int = -1  # total response byte count; -1 when not recorded
    cwd: str = ""       # where the act ran; "" on records predating the field
    tools: tuple[str, ...] = ()  # every tool in the batch, not only the first
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunView:
    run_id: str
    presence_id: str
    kind: str
    label: str
    name: str
    stream: str
    repo_label: str
    parent_run_id: str
    is_subspawn: bool
    runner_name: str
    runner_shell: str
    runner_core: str
    runner_class: str
    started_at: float
    last_seen: float
    event_id: str = ""
    prompt: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    boot: dict[str, Any] = field(default_factory=dict)
    wake_manifest: list[dict[str, Any]] = field(default_factory=list)
    boundaries: tuple[Boundary, ...] = ()
    portal_state: dict[str, Any] = field(default_factory=dict)
    inbox_state: Any = field(default_factory=list)
    card: str = ""
    thread: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ConsoleSnapshot:
    repo_root: Path
    brr_dir: Path
    daemon_pid: int | None
    runs: tuple[RunView, ...]
    selected_run_id: str | None
    selected: RunView | None
    console_key: str
    console_thread: tuple[dict[str, Any], ...]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _read_boundaries(path: Path) -> tuple[Boundary, ...]:
    if not path.is_file():
        return ()
    out: list[Boundary] = []
    for raw in _read_text(path).splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            record = {"phase": "?", "inject": raw, "malformed": True}
        if not isinstance(record, dict):
            record = {"phase": "?", "inject": str(record), "malformed": True}
        raw_out = int(record["out_bytes"]) if isinstance(
            record.get("out_bytes"), (int, float)
        ) else -1
        raw_tools = record.get("tools")
        tools = tuple(
            str(name) for name in raw_tools if isinstance(name, str) and name.strip()
        ) if isinstance(raw_tools, list) else ()
        out.append(
            Boundary(
                seq=len(out) + 1,
                phase=str(record.get("phase") or "?"),
                at=str(record.get("at") or record.get("ts") or "?"),
                act=str(record.get("act") or ""),
                inject=str(record.get("inject") or ""),
                block=bool(record.get("block")),
                block_reason=str(record.get("block_reason") or ""),
                detail=str(record.get("detail") or ""),
                out_bytes=raw_out,
                cwd=str(record.get("cwd") or ""),
                tools=tools,
                raw=dict(record),
            )
        )
    return tuple(out)


def _manifest(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    return protocol.parse_frontmatter(text) if text else {}


def _read_wake_manifest(path: Path) -> list[dict[str, Any]]:
    """Read wake-manifest.json and return its blocks list, or [] on any error.

    Returns an empty list for runs that predate the manifest (file absent)
    and for any parse error — never raises.  Callers use the empty list as
    the "no manifest" sentinel.
    """
    raw = _read_json(path, {})
    if not isinstance(raw, dict):
        return []
    blocks = raw.get("blocks")
    if not isinstance(blocks, list):
        return []
    return [b for b in blocks if isinstance(b, dict)]


def _phase_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def _boot_evidence(
    *,
    run_dir: Path,
    prompt: str,
    boundaries: tuple[Boundary, ...],
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Return facts about the daemon → Shell → model boot, with provenance.

    This is intentionally *not* a re-render of the current runner profile.
    Profiles can change after a run starts, and a reconstructed argv would then
    answer "what would launch now?" rather than "what launched this run?".

    Today two durable facts cross that honesty bar:

    * ``prompt.md`` — exact brnrd-owned bytes handed to the runner invocation;
    * a ``session-start`` boundary — native runner lifecycle evidence that the
      selected Shell started and called the brnrd hook endpoint.

    The presence registry supplies the daemon's selected Runner/Shell/Core. It
    is labelled ``daemon`` rather than ``native`` because selection is not the
    same as the Shell attesting the model-side envelope. Runner-owned base
    instructions, tool schema, project loading, and transcript transformations
    therefore stay explicitly opaque until a runner-specific receipt exists.
    """
    prompt_bytes = prompt.encode("utf-8")
    session_start = next(
        (
            edge
            for edge in boundaries
            if _phase_key(edge.phase) == "sessionstart"
        ),
        None,
    )

    native: dict[str, Any] | None = None
    if session_start is not None:
        native = {
            "provenance": "native",
            "boundary": session_start.seq,
            "at": session_start.at,
            "phase": session_start.phase,
            "act": session_start.act or None,
            "inject": session_start.inject or None,
            # Keep the whole hook-owned record available to forensic readers.
            # The TUI renders a compact summary and the JSON probe exposes this
            # exact object without defining another schema for runner events.
            "raw": session_start.raw,
        }

    return {
        "wake": {
            "provenance": "exact",
            "path": str(run_dir / "prompt.md"),
            "bytes": len(prompt_bytes),
            "sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "present": bool(prompt),
        },
        "runner": {
            "provenance": "daemon",
            "name": str(entry.get("runner_name") or ""),
            "shell": str(entry.get("runner_shell") or ""),
            "core": str(entry.get("runner_core") or ""),
            "class": str(entry.get("runner_class") or ""),
        },
        "session_start": native,
        "model_envelope": {
            "provenance": "opaque",
            "status": "not-attested",
            "note": (
                "runner/model-owned base instructions, tool schema, project "
                "loading and transcript transforms are not captured by the "
                "current durable run artifacts"
            ),
        },
    }


def console_conversation_key(repo_root: Path, repo_label: str = "") -> str:
    """Stable future local-console conversation key for this checkout.

    Conversation keys route context; they do not imply a delivery gate. This
    helper reserves the identity the local route can use later without making
    the unsafe claim that the key itself prevents fallback delivery.
    """
    label = repo_label.strip() or repo_root.name or "repo"
    safe = _CONSOLE_KEY_SAFE.sub("_", label).strip("_") or "repo"
    return f"console:{safe}"


def _run_from_presence(
    brr_dir: Path,
    entry: dict[str, Any],
    *,
    thread_limit: int,
) -> RunView | None:
    run_id = str(entry.get("run_id") or "").strip()
    if not run_id:
        return None

    run_dir = brr_dir / "runs" / run_id
    manifest = _manifest(run_dir / "run.md")
    event_id = str(manifest.get("event_id") or "").strip()
    outbox_dir = brr_dir / "outbox" / event_id if event_id else Path()
    prompt = _read_text(run_dir / "prompt.md")
    boundaries = _read_boundaries(run_dir / "boundaries.jsonl")

    stream = str(entry.get("stream") or "").strip()
    thread: tuple[dict[str, Any], ...] = ()
    if stream:
        # ``read_recent`` is the canonical kind-aware projection: it keeps
        # dialogue and excludes lifecycle rows by default. Reuse it instead of
        # copying that classification into the console.
        thread = tuple(
            conversations.read_recent(brr_dir, stream, limit=thread_limit)
        )

    return RunView(
        run_id=run_id,
        presence_id=str(entry.get("id") or ""),
        kind=str(entry.get("kind") or "run"),
        label=str(entry.get("label") or ""),
        name=str(entry.get("name") or ""),
        stream=stream,
        repo_label=str(entry.get("repo_label") or ""),
        parent_run_id=str(entry.get("parent_run_id") or ""),
        is_subspawn=bool(entry.get("is_subspawn")),
        runner_name=str(entry.get("runner_name") or ""),
        runner_shell=str(entry.get("runner_shell") or ""),
        runner_core=str(entry.get("runner_core") or ""),
        runner_class=str(entry.get("runner_class") or ""),
        started_at=float(entry.get("started_at") or 0),
        last_seen=float(entry.get("last_seen") or 0),
        event_id=event_id,
        prompt=prompt,
        manifest=manifest,
        boot=_boot_evidence(
            run_dir=run_dir,
            prompt=prompt,
            boundaries=boundaries,
            entry=entry,
        ),
        wake_manifest=_read_wake_manifest(run_dir / "wake-manifest.json"),
        boundaries=boundaries,
        portal_state=(
            _read_json(outbox_dir / "portal-state.json", {})
            if event_id else {}
        ),
        inbox_state=(
            _read_json(outbox_dir / "inbox.json", [])
            if event_id else []
        ),
        card=_read_text(outbox_dir / ".card") if event_id else "",
        thread=thread,
    )


def _pick_default(runs: list[RunView]) -> str | None:
    if not runs:
        return None
    # Prefer the resident thought over a dispatched child; newest resident
    # wins when two independent local sessions overlap.
    residents = [run for run in runs if not run.is_subspawn]
    pool = residents or runs
    return max(pool, key=lambda run: run.started_at).run_id


def collect_snapshot(
    repo_root: Path,
    *,
    selected_run_id: str | None = None,
    brr_dir: Path | None = None,
    thread_limit: int = 40,
) -> ConsoleSnapshot:
    repo_root = Path(repo_root).resolve()
    runtime = Path(brr_dir) if brr_dir is not None else gitops.shared_brr_dir(repo_root)

    active = presence.list_active(runtime)
    runs = [
        run
        for entry in active
        if (run := _run_from_presence(runtime, entry, thread_limit=thread_limit))
        is not None
    ]
    runs.sort(key=lambda run: run.started_at)

    ids = {run.run_id for run in runs}
    chosen = selected_run_id if selected_run_id in ids else _pick_default(runs)
    selected = next((run for run in runs if run.run_id == chosen), None)

    repo_label = selected.repo_label if selected else ""
    key = console_conversation_key(repo_root, repo_label)
    console_thread = tuple(
        conversations.read_recent(runtime, key, limit=thread_limit)
    )

    return ConsoleSnapshot(
        repo_root=repo_root,
        brr_dir=runtime,
        daemon_pid=daemon.read_pid(runtime),
        runs=tuple(runs),
        selected_run_id=chosen,
        selected=selected,
        console_key=key,
        console_thread=console_thread,
    )


def resolve_repo_root(path: str | os.PathLike[str] | None = None) -> Path:
    if path is None:
        return gitops.ensure_git_repo()
    candidate = Path(path).expanduser().resolve()
    # ``ensure_git_repo`` is cwd-based; for the operator console a path flag
    # is useful enough to resolve directly with git while staying dependency
    # free and without changing the caller's cwd.
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"not a git repository: {candidate}")
    return Path(result.stdout.strip()).resolve()
