"""The outbox is a verb table — the drain against a baseline captured from ``main``.

``daemon._drain_outbox`` was split into ``brr.outbox`` (one handler per
frontmatter key, a precedence table, a thin composition) with *no behaviour
change* as the contract. The existing suite is the first proof; this module is
the second: every scenario below was driven through ``_drain_outbox`` on
``main`` **before** the split, and what it produced — the return value, the
stats, the packets emitted in order, the task's meta, and every file the drain
left under the scenario's tree (inbox events, partials, notices, message-store
rows, the conversation log, ``.processed/``) — was normalised and frozen under
``tests/fixtures/outbox_golden/``. The same drive through the table must
reproduce each file exactly.

Two notice fields are *added* by the split (``source_file`` · ``verb``, the
correlation gap ``portals.md`` names). The capture strips them, so the golden
stays a statement about behaviour ``main`` had; ``test_outbox_notices.py``
pins their presence.

Regenerating a golden (``BRR_OUTBOX_GOLDEN_WRITE=1``) is only honest on a
tree whose drain is the one the golden claims to describe.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from brr import account, daemon, protocol
from brr.run import Run

GOLDEN_DIR = Path(
    os.environ.get("BRR_OUTBOX_GOLDEN_DIR")
    or Path(__file__).parent / "fixtures" / "outbox_golden"
)
WRITE = os.environ.get("BRR_OUTBOX_GOLDEN_WRITE") == "1"

_ADDED_NOTICE_FIELDS = frozenset({"source_file", "verb", "run"})

_EVT_RE = re.compile(r"evt-\d{10,}-[a-z0-9]{4}")
_RUN_ID_RE = re.compile(r"run-\d{6}-\d{4}-[a-z0-9]{4}")
_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_STAMP_RE = re.compile(r"\b\d{6}-\d{6}\b")
_CLOCK_RE = re.compile(r"\b\d{2}:\d{2}Z")
# The card's short id (`12:51Z → 668o: …`): the event's random tail.
_SHORT_ID_RE = re.compile(r"(<CLOCK> → )[a-z0-9]{4}:")
_EPOCH_RE = re.compile(r"\b1[6-9]\d{8,17}(?:\.\d+)?\b")
_HEX_TOKEN_RE = re.compile(r"\b[0-9a-f]{8,64}\b")
_VOLATILE_KEYS = frozenset({
    "ts", "at", "created", "updated", "generated_at", "change_token", "pid",
    "armed_at", "generation", "deadline", "mtime", "hold_correspondent_at",
    "created_at", "updated_at", "staged_at", "delivered_at", "also_at",
    "observed_at", "made_at", "transitioned_at", "since",
})


class _Ids:
    """Event ids are ``evt-<time_ns>-<rand>``; creation order is the name."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def collect(self, text: str) -> None:
        for match in _EVT_RE.findall(text):
            self.seen.setdefault(match, "")

    def finish(self) -> None:
        for n, eid in enumerate(sorted(self.seen), start=1):
            self.seen[eid] = f"<EVT{n}>"

    def sub(self, text: str) -> str:
        return _EVT_RE.sub(lambda m: self.seen.get(m.group(0), "<EVT?>"), text)


def _norm_str(text: str, root: str, ids: _Ids) -> str:
    text = text.replace(root, "<TMP>")
    text = ids.sub(text)
    text = _RUN_ID_RE.sub("<RUN>", text)
    text = _ISO_RE.sub("<TS>", text)
    text = _STAMP_RE.sub("<STAMP>", text)
    text = _CLOCK_RE.sub("<CLOCK>", text)
    text = _SHORT_ID_RE.sub(r"\1<TAIL>:", text)
    text = _EPOCH_RE.sub("<EPOCH>", text)
    text = _HEX_TOKEN_RE.sub("<HEX>", text)
    return text


def _norm(value: Any, root: str, ids: _Ids, *, key: str = "") -> Any:
    if key in _VOLATILE_KEYS and value not in (None, "", [], {}):
        return "<V>"
    if isinstance(value, dict):
        return {
            _norm_str(str(k), root, ids): _norm(v, root, ids, key=str(k))
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_norm(v, root, ids) for v in value]
    if isinstance(value, set):
        return sorted(_norm(v, root, ids) for v in value)
    if isinstance(value, Path):
        return _norm_str(str(value), root, ids)
    if isinstance(value, str):
        return _norm_str(value, root, ids)
    if isinstance(value, float):
        return "<F>"
    return value


def _read_tree(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if ".git" in path.relative_to(root).parts:
            continue  # object hashes carry commit clocks; the refs are git's
        rel = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            out[rel] = "<binary>"
            continue
        if path.name == daemon.NOTICES_FILE:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            out[rel] = [
                {k: v for k, v in row.items() if k not in _ADDED_NOTICE_FIELDS}
                for row in rows
            ]
        elif path.suffix == ".jsonl":
            out[rel] = [json.loads(line) for line in text.splitlines() if line.strip()]
        elif path.suffix == ".json":
            try:
                out[rel] = json.loads(text)
            except ValueError:
                out[rel] = text
        else:
            out[rel] = text
    return out


def _capture(tmp_path: Path, promoted: int, stats: dict, emitted: list, task: Any) -> dict:
    snapshot = {
        "promoted": promoted,
        "stats": dict(sorted(stats.items())),
        "emitted": [{"type": p.type, "payload": p.payload} for p in emitted],
        "task_meta": dict(getattr(task, "meta", {}) or {}),
        "tree": _read_tree(tmp_path),
    }
    ids = _Ids()
    raw = json.dumps(snapshot, default=str, sort_keys=True)
    ids.collect(raw)
    # Paths carry ids too; the tree keys are strings inside `raw` already.
    ids.finish()
    return _norm(json.loads(raw), str(tmp_path), ids)


# ── the scenarios ──────────────────────────────────────────────────────


def _base(tmp_path: Path, *, source: str = "telegram", own_meta: dict | None = None):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True)
    own = protocol.create_event(
        inbox, source, "do the thing", status="processing",
        telegram_user_id="42", telegram_chat_id="42", **(own_meta or {}),
    )
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    return brr_dir, inbox, responses, outbox, own.stem


def _drive(
    tmp_path: Path,
    monkeypatch,
    stage: Callable[[Path, Path, str], None],
    *,
    source: str = "telegram",
    task_meta: dict | None = None,
    with_account: bool = False,
    patch: Callable[[Any], None] | None = None,
) -> dict:
    brr_dir, inbox, responses, outbox, own_id = _base(tmp_path, source=source)
    stage(inbox, outbox, own_id)
    emitted: list = []
    monkeypatch.setattr(daemon.updates, "emit", lambda _brr, pkt: emitted.append(pkt))
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram")
    if patch is not None:
        patch(monkeypatch)
    ctx = None
    meta = dict(task_meta or {})
    if with_account:
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        ctx = account.resolve_context(
            repo, {"home.path": str(tmp_path / "home"), "repo.label": "Gurio/brr"},
        )
        meta.setdefault("repo_label", "Gurio/brr")
    task = Run(
        id="run-parent", event_id=own_id, body="do the thing", source=source,
        meta=meta,
    )
    task.conversation_key = "telegram:42:"
    stats: dict[str, int] = {}
    emit = daemon._WorkerEmit(
        brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own_id,
    )
    promoted = daemon._drain_outbox(
        emit, task, responses, own_id, outbox, inbox,
        repo_root=None, account_context=ctx, stats=stats,
    )
    return _capture(tmp_path, promoted, stats, emitted, task)


def _write(outbox: Path, name: str, text: str) -> None:
    (outbox / name).write_text(text, encoding="utf-8")


def _scenario_event_reply_with_also(inbox, outbox, _own):
    lead = protocol.create_event(
        inbox, "telegram", "one", telegram_user_id="42", telegram_chat_id="42",
    )
    second = protocol.create_event(
        inbox, "telegram", "two", telegram_user_id="42", telegram_chat_id="42",
    )
    _write(
        outbox, "reply.md",
        f"---\nevent: {lead.stem}\nalso: {second.stem}\n---\ngot both — on it\n",
    )


def _scenario_note(inbox, outbox, _own):
    other = protocol.create_event(
        inbox, "telegram", "fyi", telegram_user_id="42", telegram_chat_id="42",
    )
    _write(outbox, "note.md", f"---\nnote: {other.stem}\n---\nignored body\n")


def _scenario_spawn_accepted(_inbox, outbox, _own):
    _write(
        outbox, "spawn.md",
        "---\nspawn: true\nshell: claude\nbranch: brr/child\n"
        "report: /tmp/child-report.md\ntitle: a child\n---\nchild task\n",
    )


def _scenario_spawn_refused_env_floor(_inbox, outbox, _own):
    _write(
        outbox, "spawn-host.md",
        "---\nspawn: true\nshell: claude\nenvironment: host\n---\nshare my cwd\n",
    )


def _scenario_stop_converged(_inbox, outbox, _own):
    # No live control for the id: the child already finished (converged).
    _write(outbox, "stop.md", "---\nstop: evt-ghost\n---\n")


def _scenario_to_unknown(_inbox, outbox, _own):
    _write(outbox, "steer.md", "---\nto: evt-nobody\n---\nturn left\n")


def _scenario_await(_inbox, outbox, _own):
    _write(outbox, "await.md", "---\nawait: true\ntimeout: 5m\n---\n")


def _scenario_no_recognised_key(_inbox, outbox, _own):
    _write(outbox, "plain.md", "---\nlabel: x\n---\njust a note to the thread\n")


def _scenario_bare_body(_inbox, outbox, _own):
    _write(outbox, "001.md", "first\n")
    _write(outbox, "002.md", "second\n")


def _scenario_gate_undeliverable(_inbox, outbox, _own):
    _write(outbox, "gate.md", "---\ngate: slack\n---\nping\n")


def _scenario_hold_refused(_inbox, outbox, _own):
    _write(outbox, "hold.md", "---\nhold: true\nresume: operator\nreason: bored\n---\n")


def _scenario_cut_parse_error(_inbox, outbox, _own):
    _write(outbox, "cut.md", "---\ncut: true\nproduce: [\n---\nbye\n")


def _scenario_note_unknown(_inbox, outbox, _own):
    _write(outbox, "note.md", "---\nnote: evt-1234567890123-zzzz\n---\n")


def _scenario_precedence_spawn_over_to_and_note(_inbox, outbox, _own):
    _write(
        outbox, "many.md",
        "---\nnote: evt-nothing\nto: evt-nobody\nspawn: true\nshell: claude\n"
        "environment: host\n---\nwhich wins\n",
    )


def _scenario_submit_from_resident(_inbox, outbox, _own):
    _write(outbox, "submit.md", "---\nsubmit: true\n---\nhere\n")


def _scenario_ask_allowance_from_resident(_inbox, outbox, _own):
    _write(outbox, "ask.md", "---\nask: allowance +50k\n---\nmore room\n")


def _scenario_empty_cut_casualty(_inbox, outbox, _own):
    _write(outbox, "do-1-cut-2.md", "no frontmatter at all\n")


def _scenario_staging_and_dotfiles(_inbox, outbox, _own):
    _write(outbox, "note.md.tmp.1.abc", "---\nnote: x\n---\n")
    _write(outbox, ".keepalive", "1\n")


SCENARIOS: dict[str, dict[str, Any]] = {
    "event_reply_with_also": {"stage": _scenario_event_reply_with_also, "with_account": True},
    "note": {"stage": _scenario_note},
    "note_unknown": {"stage": _scenario_note_unknown},
    "spawn_accepted": {"stage": _scenario_spawn_accepted},
    "spawn_refused_env_floor": {"stage": _scenario_spawn_refused_env_floor},
    "stop_converged": {"stage": _scenario_stop_converged},
    "to_unknown": {"stage": _scenario_to_unknown},
    "await": {"stage": _scenario_await},
    "no_recognised_key": {"stage": _scenario_no_recognised_key, "with_account": True},
    "bare_body": {"stage": _scenario_bare_body},
    "gate_undeliverable": {"stage": _scenario_gate_undeliverable, "with_account": True},
    "hold_refused": {"stage": _scenario_hold_refused},
    "cut_parse_error": {"stage": _scenario_cut_parse_error},
    "precedence_spawn_over_to_and_note": {"stage": _scenario_precedence_spawn_over_to_and_note},
    "submit_from_resident": {"stage": _scenario_submit_from_resident},
    "ask_allowance_from_resident": {"stage": _scenario_ask_allowance_from_resident},
    "empty_cut_casualty": {"stage": _scenario_empty_cut_casualty},
    "staging_and_dotfiles": {"stage": _scenario_staging_and_dotfiles},
}


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_drain_reproduces_the_main_baseline(name, tmp_path, monkeypatch):
    spec = SCENARIOS[name]
    got = _drive(
        tmp_path, monkeypatch, spec["stage"],
        with_account=spec.get("with_account", False),
        task_meta=spec.get("task_meta"),
    )
    path = GOLDEN_DIR / f"{name}.json"
    rendered = json.dumps(got, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if WRITE:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return
    assert path.exists(), f"no golden for {name} — capture on the unsplit drain"
    assert rendered == path.read_text(encoding="utf-8")
