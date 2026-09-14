"""`fold: <place>` — the frame opens a bench file and asks the weaver to fill it.

design-the-loom §5/§6/§18: a fold is perception, cached — a markdown file
keyed to a place in the tree and the commit it was read at, in the home's
bench (``<home>/bench/<repo>/<place>/<commit>.md``). The frame routes and
renders; it never interprets. So this verb does exactly two things and
stops:

1. **the store** — creates the bench file with frontmatter
   ``place · commit · question · made_at`` and an empty body. ``commit`` is
   ``HEAD`` of the repo's checkout, read at the moment of the fold. A file
   already at that address is left as it is (the weaver may have written its
   body); the ask is delivered again.
2. **the ask** — one event on the seat's own thread (``source: fold``, the
   way a schedule firing is mail to the seat) carrying the focus as flat
   frontmatter — ``focus_place · focus_commit · focus_question ·
   focus_bench_path`` — because event frontmatter is flat ``key: value``.

The weaver writes the body; that is not this verb. ``mark:`` (the user's
write on a bench file) and ``stake:``/``cut-at:`` are 4b.

Refused: a strand (the ask would land on a thread it cannot read), a place
that is absolute or climbs out with ``..``. Dropped: no account home, no
repo label, no readable ``HEAD``, no inbox.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path, PurePosixPath

from .. import account
from .. import daemon
from .. import protocol
from .shapes import Handled, OutboxFile

SOURCE = "fold"


def _refuse(f: OutboxFile, text: str, *, kind: str = "refused") -> Handled:
    from .verbs import _handled

    daemon._record_outbox_notice(f.ctx.outbox_dir, text, kind=kind, lifetime="run")
    daemon._retire_outbox_staging(f.path)
    return _handled(f, "fold", 0)


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def normalise_place(raw: str) -> str | None:
    """A repo-relative POSIX place, or ``None`` when it is not one."""
    text = raw.strip().replace("\\", "/")
    if not text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return None
    parts = [p for p in PurePosixPath(text).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) or "."


def read_head(repo_root: Path) -> str:
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True,
            text=True, timeout=30, env=env, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def bench_path(home: Path, repo_slug: str, place: str, commit: str) -> Path:
    base = home / "bench" / repo_slug
    if place != ".":
        base = base.joinpath(*place.split("/"))
    return base / f"{commit}.md"


def handle(f: OutboxFile) -> Handled:
    from .verbs import _handled

    task = f.run
    meta = getattr(task, "meta", None) or {}
    raw = _one_line(f.frontmatter.get("fold"))
    place = normalise_place(raw)
    if place is None:
        return _refuse(
            f, f"fold refused: place {raw!r} is not a path inside the repo — "
            "write it relative to the repo root, without `..`",
        )
    if daemon._is_strand(meta):
        return _refuse(
            f, f"fold refused: {place} — a fold's ask lands on the seat's thread, "
            "which a strand cannot read; name the place in your return value instead",
        )
    ctx = f.ctx.account_context
    if ctx is None:
        return _refuse(f, f"fold dropped: {place} — no account home to hold the bench", kind="dropped")
    label = str(meta.get("repo_label") or "")
    if not label and getattr(ctx, "default_repo", None) is not None:
        label = str(getattr(ctx.default_repo, "label", "") or "")
    if not label:
        return _refuse(f, f"fold dropped: {place} — this run has no repo label", kind="dropped")
    if f.ctx.repo_root is None:
        return _refuse(f, f"fold dropped: {place} — this run has no repo checkout", kind="dropped")
    commit = read_head(f.ctx.repo_root)
    if not commit:
        return _refuse(
            f, f"fold dropped: {place} — `git rev-parse HEAD` did not answer in the checkout",
            kind="dropped",
        )
    if f.ctx.inbox_dir is None:
        return _refuse(f, f"fold dropped: {place} — no inbox to deliver the ask to", kind="dropped")

    question = _one_line(f.frontmatter.get("question"))
    home = account.context_home_root(ctx)
    path = bench_path(home, account.slug_repo_label(label), place, commit)
    made_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existed = path.exists()
    if not existed:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "---\n"
                f"place: {place}\n"
                f"commit: {commit}\n"
                f"question: {question}\n"
                f"made_at: {made_at}\n"
                "---\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return _refuse(f, f"fold dropped: {place} — could not write {path.name}: {exc}", kind="dropped")

    conversation_key = str(getattr(task, "conversation_key", "") or f.ctx.emit.conversation_key or "")
    lines = [f"fold {place} @ {commit[:10]}"]
    if question:
        lines.append(f"question: {question}")
    lines.append(f"bench: {path}" + (" (already there — left as it was)" if existed else ""))
    try:
        event_path = protocol.create_event(
            f.ctx.inbox_dir, "fold", "\n\n".join(lines),
            conversation_key=_one_line(conversation_key),
            repo_label=_one_line(label),
            focus_place=place,
            focus_commit=commit,
            focus_question=question,
            focus_bench_path=str(path),
            fold_by_run=str(getattr(task, "id", "") or ""),
        )
    except (OSError, ValueError) as exc:
        # The store half already happened; say where, so the ask can be
        # re-issued against the same file rather than lost behind a generic
        # drain error.
        return _refuse(
            f, f"fold dropped: {place} — the bench file is at {path} but the ask "
            f"was not delivered: {exc} — re-issue `fold: {place}` to ask again",
            kind="dropped",
        )
    f.ctx.emit(
        "fold_requested",
        run_id=getattr(task, "id", ""),
        event_id=f.ctx.event_id,
        fold_event=event_path.stem,
        place=place,
        commit=commit,
        bench_path=str(path),
    )
    stats = f.ctx.stats
    if stats is not None:
        stats["fold"] = stats.get("fold", 0) + 1
    daemon._retire_outbox_staging(f.path)
    return _handled(f, "fold", 1)
