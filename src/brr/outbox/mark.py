"""`mark: keep|drop <bench path>` — the user's one write on the bench (move 4b).

design-the-loom §6, contract sentence 3: *the user's only writes are the mark
on a plaque and the warp's intent.* The frame routes and records; it never
decides what a fold was worth. The user marks from the dashboard's bench page
or the chat; the resident relays it here (and may mark on its own account).

What the verb does, and only this:

1. **the mark** — appends ``mark: keep`` / ``mark: drop`` to the bench file's
   frontmatter: repeated lines, the bench store's own grammar
   (``brnrd.bench_store`` reads them into ``marks``). One ``advisory`` notice
   and one ``card`` produce row (``runs/<run>/produce.jsonl``, the ledger
   ``land:`` writes).
2. **keep promotes** — one event on the seat's own thread (``source: mark``,
   the way ``fold:`` delivers its ask) carrying ``focus_bench_path`` and
   ``focus_place``: *write the kb page from this fold*. The weaver writes the
   page; that is not this verb. When the weaver's reply to that ask names the
   page — the reply's ``page:`` frontmatter, or a ``page`` produce row in the
   run — :func:`promotion_target` / :func:`record_promotion` append
   ``promoted_to: <kb path>`` to the bench file.
3. **drop moves** the file under ``bench/<repo>/dropped/``, its place kept
   beneath (``dropped/<place>/<commit>.md``) so two drops never collide.

Paths: absolute inside ``<home>/bench/``, or relative to it (``<repo>/<place>/
<commit>``, the dashboard's ``path``, with or without ``.md``; a leading
``bench/`` is tolerated). Refused: outside the bench, no such file, a file
already dropped, a verdict that is not ``keep``/``drop``, a strand (the ask
would land on a thread it cannot read).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .. import account
from .. import daemon
from .. import protocol
from .. import run_topic
from ..hud import PRODUCE_LEDGER_NAME
from .shapes import Handled, OutboxFile, Produce

SOURCE = "mark"
VERDICTS = ("keep", "drop")
DROPPED_DIR = "dropped"

_FRONTMATTER_OPEN = "---\n"


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _refuse(f: OutboxFile, text: str, *, kind: str = "refused") -> Handled:
    from .verbs import _handled

    daemon._record_outbox_notice(f.ctx.outbox_dir, text, kind=kind, lifetime="run")
    daemon._retire_outbox_staging(f.path)
    return _handled(f, "mark", 0)


def parse(raw: object) -> tuple[str, str]:
    """``(verdict, path)`` from ``keep <path>``; either may be empty."""
    text = _one_line(raw)
    verdict, _, rest = text.partition(" ")
    return verdict.strip().lower(), rest.strip().strip("`")


def resolve(bench_root: Path, raw: str) -> Path | None:
    """The bench file *raw* names, or ``None`` when it is not inside the bench.

    Existence is the caller's check; containment is decided on the resolved
    path, so a ``..`` or a symlink out of the bench is outside it.
    """
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
        if parts and parts[0] == "bench":
            parts = parts[1:]
        if not parts:
            return None
        candidate = bench_root.joinpath(*parts)
    if not candidate.name.endswith(".md"):
        candidate = candidate.with_name(candidate.name + ".md")
    try:
        candidate.resolve().relative_to(bench_root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def read_frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {k: str(v) for k, v in protocol.parse_frontmatter(text).items()}


def append_line(path: Path, key: str, value: str) -> None:
    """Append ``key: value`` as the last frontmatter line (atomic rename).

    A file with no frontmatter gains one. Repeated keys are the grammar:
    nothing already there is rewritten.
    """
    text = path.read_text(encoding="utf-8")
    line = f"{key}: {_one_line(value)}\n"
    if text.startswith(_FRONTMATTER_OPEN):
        close = text.find("\n---", len(_FRONTMATTER_OPEN) - 1)
        if close != -1:
            head = text[: close + 1]
            new = head + line + text[close + 1:]
        else:
            new = _FRONTMATTER_OPEN + line + "---\n" + text
    else:
        new = _FRONTMATTER_OPEN + line + "---\n" + text
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)


def _write_produce(brr_dir: Path, run_id: str, row: dict[str, Any]) -> None:
    if not run_id:
        return
    path = Path(brr_dir) / "runs" / run_id / PRODUCE_LEDGER_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def handle(f: OutboxFile) -> Handled:
    from .verbs import _handled

    task = f.run
    meta = getattr(task, "meta", None) or {}
    verdict, raw_path = parse(f.frontmatter.get("mark"))
    if verdict not in VERDICTS:
        return _refuse(
            f, f"mark dropped: `mark: {_one_line(f.frontmatter.get('mark'))}` — write "
            "`mark: keep <bench path>` or `mark: drop <bench path>`",
            kind="dropped",
        )
    if daemon._is_strand(meta):
        return _refuse(
            f, f"mark refused: {raw_path} — a mark's promotion ask lands on the seat's "
            "thread, which a strand cannot read; name the file in your return value",
        )
    ctx = f.ctx.account_context
    if ctx is None:
        return _refuse(f, f"mark dropped: {raw_path} — no account home holds a bench", kind="dropped")
    home = account.context_home_root(ctx)
    bench_root = home / "bench"
    path = resolve(bench_root, raw_path)
    if path is None:
        return _refuse(f, f"mark refused: {raw_path!r} is not a path inside the bench ({bench_root})")
    if not path.is_file():
        return _refuse(f, f"mark refused: no bench file at {path}")
    rel = path.relative_to(bench_root)
    if len(rel.parts) < 2:
        return _refuse(f, f"mark refused: {rel.as_posix()} is not a fold (`<repo>/<place>/<commit>.md`)")
    if len(rel.parts) >= 3 and rel.parts[1] == DROPPED_DIR:
        return _refuse(f, f"mark refused: {rel.as_posix()} is already dropped")

    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        append_line(path, "mark", verdict)
    except OSError as exc:
        return _refuse(f, f"mark dropped: {rel.as_posix()} — could not append the mark: {exc}", kind="dropped")
    front = read_frontmatter(path)
    place = front.get("place") or "/".join(rel.parts[1:-1]) or "."
    run_id = str(getattr(task, "id", "") or "")
    ref = (Path("bench") / rel).as_posix()
    topic = run_topic.act_topic(task) or ""
    detail = ""

    if verdict == "drop":
        dropped = bench_root / rel.parts[0] / DROPPED_DIR / Path(*rel.parts[1:])
        try:
            dropped.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, dropped)
        except OSError as exc:
            return _refuse(
                f, f"mark dropped: {rel.as_posix()} — marked drop, but the move failed: {exc}",
                kind="dropped",
            )
        ref = (Path("bench") / dropped.relative_to(bench_root)).as_posix()
        detail = f" → {dropped.relative_to(bench_root).as_posix()}"
    else:
        if f.ctx.inbox_dir is None:
            return _refuse(
                f, f"mark dropped: {rel.as_posix()} — marked keep, but no inbox to deliver "
                "the promotion ask to; re-issue the mark to ask again",
                kind="dropped",
            )
        label = str(meta.get("repo_label") or "")
        conversation_key = str(getattr(task, "conversation_key", "") or f.ctx.emit.conversation_key or "")
        lines = [
            f"keep {place} — promote this fold to a kb page",
            f"bench: {path}",
            "Write the page from the fold, then answer this ask with `page: <kb path>` "
            "in the reply's frontmatter; the frame records it on the bench file.",
        ]
        try:
            event_path = protocol.create_event(
                f.ctx.inbox_dir, "mark", "\n\n".join(lines),
                conversation_key=_one_line(conversation_key),
                repo_label=_one_line(label),
                focus_place=_one_line(place),
                focus_commit=_one_line(front.get("commit") or path.stem),
                focus_bench_path=str(path),
                mark_verdict="keep",
                mark_by_run=run_id,
                **({"topic": topic} if topic else {}),
            )
        except (OSError, ValueError) as exc:
            return _refuse(
                f, f"mark dropped: {rel.as_posix()} — marked keep, but the promotion ask "
                f"was not delivered: {exc}; re-issue the mark to ask again",
                kind="dropped",
            )
        detail = f" — promotion asked ({event_path.stem})"
        f.ctx.emit(
            "mark_promotion_requested",
            run_id=run_id, event_id=f.ctx.event_id, mark_event=event_path.stem,
            bench_path=str(path), place=place,
        )

    row: dict[str, Any] = {
        "kind": "card", "ref": ref, "at": at, "verb": "mark", "mark": verdict,
        "place": place, "by": "frame",
    }
    if topic:
        row["topic"] = topic
    _write_produce(f.ctx.emit.brr_dir, run_id, row)
    daemon._record_outbox_notice(
        f.ctx.outbox_dir, f"mark {verdict}: {rel.as_posix()}{detail}",
        kind="advisory", lifetime="run",
    )
    stats = f.ctx.stats
    if stats is not None:
        stats["mark"] = stats.get("mark", 0) + 1
    daemon._retire_outbox_staging(f.path)
    return _handled(f, "mark", 1, produce=(Produce(kind="card", ref=ref, at=at),))


# ── the promotion's receipt: `promoted_to` on the reply ────────────────


def _page_from_produce(brr_dir: Path, run_id: str) -> str:
    """The last ``page`` produce row this run wrote, or ``""``."""
    if not run_id:
        return ""
    path = Path(brr_dir) / "runs" / run_id / PRODUCE_LEDGER_NAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") == "page" and row.get("ref"):
            return _one_line(row["ref"])
    return ""


def promotion_target(f: OutboxFile) -> tuple[Path, str] | None:
    """Before an ``event:`` reply is delivered: is it the answer to a keep's
    ask that names its page? ``(bench file, page)`` or ``None``.

    Read before the reply handler runs — once delivered, the ask is no
    longer pending and does not resolve.
    """
    if f.ctx.account_context is None:
        return None
    target = str(f.frontmatter.get("event") or "").strip() or str(f.ctx.event_id or "")
    if not target:
        return None
    try:
        event, _resp, _amb = daemon._resolve_event_target(f.ctx.address_sources or [], target)
    except Exception:  # noqa: BLE001 - a lookup never costs the reply
        return None
    if event is None or str(event.get("source") or "") != SOURCE:
        return None
    bench = str(event.get("focus_bench_path") or "").strip()
    if not bench:
        return None
    page = _one_line(f.frontmatter.get("page")) or _page_from_produce(
        f.ctx.emit.brr_dir, str(getattr(f.run, "id", "") or ""),
    )
    if not page:
        return None
    return Path(bench), page


def record_promotion(f: OutboxFile, target: tuple[Path, str]) -> None:
    path, page = target
    try:
        home = account.context_home_root(f.ctx.account_context)
        path.resolve().relative_to((home / "bench").resolve())
    except (OSError, ValueError, TypeError):
        return
    if not path.is_file():
        daemon._record_outbox_notice(
            f.ctx.outbox_dir,
            f"mark: the reply names page {page}, but the bench file {path} is gone — "
            "nothing recorded",
            kind="advisory", lifetime="run",
        )
        return
    try:
        append_line(path, "promoted_to", page)
    except OSError:
        return
    daemon._record_outbox_notice(
        f.ctx.outbox_dir, f"mark keep: {path.name} promoted_to {page}",
        kind="advisory", lifetime="run",
    )
