"""Live progress card — post / patch a comment per run-update packet.

On ``run_created`` the gate posts a fresh comment on the originating
issue or PR and records its comment ID so later packets can edit the
same comment in place. Failures are swallowed so the daemon keeps
running even if the GitHub API is unreachable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import run_progress
from ...run import Run, run_manifest_path
from . import client, state
from .constants import _RENDERABLE_PACKETS
from .delivery import _coerce_int
from .paths import issue_comment, issue_comments


def _progress_state_path(brr_dir: Path, run_id: str) -> Path:
    safe = run_id.replace("/", "_").replace("..", "_")
    return brr_dir / "gates" / "github" / "progress" / f"{safe}.json"


def _load_progress_for_run(brr_dir: Path, run_id: str) -> dict | None:
    path = _progress_state_path(brr_dir, run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_progress_for_run(brr_dir: Path, run_id: str, data: dict) -> None:
    path = _progress_state_path(brr_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_card_text(brr_dir: Path, conv_key: str, run_id: str) -> str | None:
    view = run_progress.project_run(brr_dir, conv_key, run_id)
    if view is None:
        return None
    return run_progress.render_text(
        view,
        compact=True,
        style=run_progress.GITHUB_MARKDOWN_STYLE,
    )


def render_update(brr_dir: Path, packet: Any) -> None:
    """Create / edit a GitHub progress comment for *packet*."""
    ptype = getattr(packet, "type", None)
    if ptype not in _RENDERABLE_PACKETS:
        return

    state_dict = state._load_state(brr_dir)
    token = state.resolve_token(state_dict)
    if not token:
        return

    conv_key = getattr(packet, "conversation_key", "") or ""
    run_id = run_progress.run_id_from_packet(packet)
    if not conv_key or not run_id:
        return

    task = Run.from_file(run_manifest_path(brr_dir / "runs", run_id))
    if task is None or task.source != "github":
        return
    repo = task.meta.get("github_repo") or state_dict.get("repo")
    number = _coerce_int(task.meta.get("github_issue_number"))
    if not repo or number is None:
        return

    text = _build_card_text(brr_dir, conv_key, run_id)
    if text is None:
        return

    entry = _load_progress_for_run(brr_dir, run_id)

    if entry and entry.get("last_text") == text:
        entry["last_render"] = ptype
        _save_progress_for_run(brr_dir, run_id, entry)
        return

    try:
        if entry and entry.get("comment_id"):
            try:
                client._api_patch(
                    token,
                    issue_comment(repo, entry["comment_id"]),
                    body={"body": text},
                )
            except Exception:
                # Comment deleted; fall through to post a fresh one.
                new = client._api_post(
                    token,
                    issue_comments(repo, number),
                    body={"body": text},
                )
                cid = (new or {}).get("id") if isinstance(new, dict) else None
                if cid is None:
                    return
                entry = {"comment_id": cid}
        else:
            new = client._api_post(
                token,
                issue_comments(repo, number),
                body={"body": text},
            )
            cid = (new or {}).get("id") if isinstance(new, dict) else None
            if cid is None:
                return
            entry = {"comment_id": cid}

        entry["last_text"] = text
        entry["last_render"] = ptype
        _save_progress_for_run(brr_dir, run_id, entry)

    except Exception as exc:
        print(f"[brnrd:github] render_update error for {run_id}: {exc}")
