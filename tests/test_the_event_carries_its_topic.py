"""Move 5c — the event carries its topic (design-the-loom §21).

The store (``heddles``: the index, the thread map, the proposal, lit by
assignment), the run's ``.topic`` (``run_topic``: settle, inheritance, the
error row, the predecessor line), the verbs that are not covered by the drain
goldens (``fold:``, ``land:``, ``topic: show``, ``topic: assign``), the Shell
row's paths, the HUD's two keys and the boot's ask.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from brr import account, conversations, daemon, heddles, hooks, hud, protocol, run_topic
from brr.outbox import land
from brr.run import Run, run_manifest_path


@pytest.fixture(autouse=True)
def _clean():
    heddles.reset_cache()
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    heddles.reset_cache()
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _topic(home: Path, slug: str, *, words=(), places=(), produce=(), threads=(), ids=()) -> Path:
    directory = home / "surface" / "topics"
    directory.mkdir(parents=True, exist_ok=True)
    body = f"# {slug}\n"
    if ids:
        body += "\nids: " + " ".join(ids) + "\n"
    topic = heddles.Topic(
        slug=slug, path=directory / f"{slug}.md", title=slug,
        signature=heddles.Signature(
            places=tuple(places), words=tuple(words), produce=tuple(produce), threads=tuple(threads),
        ),
        body=body,
    )
    topic.path.write_text(heddles.render_topic(topic), encoding="utf-8")
    return topic.path


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── the store: the index and its reader ──────────────────────────────


def test_the_index_writer_appends_one_row_per_ref(tmp_path):
    home = tmp_path / "home"
    _topic(home, "the-loom")
    assert heddles.append_index(home, "the-loom", kind="message", ref="run-a/000001-interim",
                                at="2026-09-15T03:00:00Z", run="run-a")
    assert not heddles.append_index(home, "the-loom", kind="message", ref="run-a/000001-interim",
                                    at="2026-09-15T03:05:00Z", run="run-a")
    assert not heddles.append_index(home, "the-loom", kind="gossip", ref="x")
    assert not heddles.append_index(home, "the-loom", kind="event", ref="  ")
    assert not heddles.append_index(None, "the-loom", kind="event", ref="evt-1")
    rows = _jsonl(home / "surface" / "topics" / "the-loom.index.jsonl")
    assert rows == [{"at": "2026-09-15T03:00:00Z", "kind": "message",
                     "ref": "run-a/000001-interim", "run": "run-a"}]
    # The index is not a topic: the reader and the warp graph read `*.md` only.
    assert [t.slug for t in heddles.load_topics(home / "surface" / "topics")] == ["the-loom"]


def test_the_reader_resolves_aliases_and_merges_by_at(tmp_path):
    home = tmp_path / "home"
    # `the-weave` absorbed `old-a` and `old-b` (the merge verb's grammar).
    _topic(home, "the-weave", ids=("old-a", "old-b"))
    heddles.append_index(home, "old-a", kind="event", ref="evt-1", at="2026-09-15T01:00:00Z", run="r1")
    heddles.append_index(home, "the-weave", kind="bolt", ref="r3/bolt/x", at="2026-09-15T03:00:00Z", run="r3")
    heddles.append_index(home, "old-b", kind="strand", ref="evt-2", at="2026-09-15T02:00:00Z", run="r2")
    # the same ref in two files (assigned before the merge): the earliest wins
    heddles.append_index(home, "old-b", kind="event", ref="evt-1", at="2026-09-15T04:00:00Z", run="r4")

    whole = heddles.index(home, "the-weave")
    assert [(r["kind"], r["ref"]) for r in whole] == [
        ("event", "evt-1"), ("strand", "evt-2"), ("bolt", "r3/bolt/x"),
    ]
    assert whole[0]["run"] == "r1"
    # asking for a merged-away name reads the merged whole
    assert heddles.index(home, "old-a") == whole
    assert heddles.resolve_slug(home, "old-b") == "the-weave"
    # since: an ISO time, or a span counted back from now
    assert [r["ref"] for r in heddles.index(home, "the-weave", "2026-09-15T01:30:00Z")] == ["evt-2", "r3/bolt/x"]
    assert [r["ref"] for r in heddles.index(
        home, "the-weave", "90m", now="2026-09-15T03:10:00Z")] == ["evt-2", "r3/bolt/x"]
    assert heddles.index(home, "no-such-topic") == []


def test_the_merge_verb_keeps_the_absorbed_index_file_and_the_alias_reads_it(tmp_path, monkeypatch):
    from brr.outbox import topic as topic_verb
    from brr.outbox.shapes import DrainContext, OutboxFile

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = account.resolve_context(repo, {"home.path": str(home), "repo.label": "o/r"})
    real_home = account.context_home_root(ctx)
    _topic(real_home, "a")
    _topic(real_home, "b")
    heddles.append_index(real_home, "a", kind="event", ref="evt-a", at="2026-09-15T01:00:00Z", run="r")
    heddles.append_index(real_home, "b", kind="event", ref="evt-b", at="2026-09-15T02:00:00Z", run="r")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    staged = outbox / "merge.md"
    staged.write_text("---\ntopic: merge a, b -> c\n---\n", encoding="utf-8")
    f = OutboxFile(
        path=staged, frontmatter={"topic": "merge a, b -> c"}, body="",
        run=Run(id="run-seat", event_id="evt-x", body="", source="telegram"),
        ctx=DrainContext(
            emit=lambda *a, **k: None, responses_dir=tmp_path, event_id="evt-x",
            outbox_dir=outbox, inbox_dir=None, repo_root=None, account_context=ctx,
            stats=None, address_sources=None,
        ),
    )
    assert topic_verb.handle(f).outcome == "accepted"
    assert (real_home / "surface" / "topics" / "a.index.jsonl").exists()
    assert [r["ref"] for r in heddles.index(real_home, "c")] == ["evt-a", "evt-b"]


def test_the_thread_map_resolves_through_aliases(tmp_path):
    home = tmp_path / "home"
    _topic(home, "the-weave", ids=("the-loom",))
    assert heddles.record_thread_topic(home, "telegram:42:", "the-loom", run="r")
    assert heddles.thread_topic(home, "telegram:42:") == "the-weave"
    assert heddles.thread_topic(home, "telegram:43:") is None
    assert not heddles.record_thread_topic(home, "", "the-loom")


# ── the proposal ─────────────────────────────────────────────────────


def test_the_proposal_is_the_best_signature_match(tmp_path):
    home = tmp_path / "home"
    _topic(home, "the-loom", words=("loom", "heddle"), places=("src/brr/heddles.py",))
    _topic(home, "the-post", words=("letter",), produce=("#1975",))
    text = "the heddle on the loom — see src/brr/heddles.py and #1975"
    assert heddles.propose(home, text) == ("the-loom", "signature")  # 3 terms beat 1
    assert heddles.propose(home, "a letter about #1975") == ("the-post", "signature")


def test_the_proposal_falls_back_to_the_thread_then_a_suggestion_then_none(tmp_path):
    home = tmp_path / "home"
    _topic(home, "the-loom", words=("loom",))
    # move 5d: no match and no thread ⇒ a new slug is suggested from the text
    assert heddles.propose(home, "nothing matches here", thread="telegram:42:") == (
        "nothing-matches", "suggested")
    heddles.record_thread_topic(home, "telegram:42:", "the-loom")
    assert heddles.propose(home, "nothing matches here", thread="telegram:42:") == ("the-loom", "thread")
    assert heddles.propose(tmp_path / "empty-home", "loom", thread="telegram:42:") == ("loom", "suggested")
    assert heddles.propose(home, "???", thread="telegram:99:") == (None, "none")
    assert heddles.propose(None, "loom") == (None, "none")


def test_prepare_stamps_the_proposal_on_the_event_and_writes_nothing_without_one(tmp_path):
    import importlib

    prepare = importlib.import_module("brr.worker.prepare")

    home_repo = tmp_path / "repo"
    home_repo.mkdir()
    ctx = account.resolve_context(home_repo, {"home.path": str(tmp_path / "home"), "repo.label": "o/r"})
    home = account.context_home_root(ctx)
    _topic(home, "the-loom", words=("loom",))
    inbox = tmp_path / "inbox"
    brr_dir = tmp_path / ".brr"

    hit_path = protocol.create_event(inbox, "telegram", "the loom again")
    hit = protocol._read_event(hit_path)
    task = Run.from_event(hit)
    prepare._stamp_topic_proposal(hit, task, ctx, brr_dir, "telegram:42:")
    assert protocol._read_event(hit_path)["topic_proposed"] == "the-loom"
    assert protocol._read_event(hit_path)["topic_proposed_by"] == "signature"
    assert task.meta["topic_proposed"] == "the-loom"

    # move 5d: a miss carries a suggested new slug, never a proposal
    miss_path = protocol.create_event(inbox, "telegram", "unrelated")
    miss = protocol._read_event(miss_path)
    prepare._stamp_topic_proposal(miss, Run.from_event(miss), ctx, brr_dir, "telegram:99:")
    assert protocol._read_event(miss_path)["topic_suggested"] == "unrelated"
    assert "topic_proposed" not in protocol._read_event(miss_path)

    blank_path = protocol.create_event(inbox, "telegram", "???")
    before = blank_path.read_text(encoding="utf-8")
    blank = protocol._read_event(blank_path)
    prepare._stamp_topic_proposal(blank, Run.from_event(blank), ctx, brr_dir, "telegram:98:")
    assert blank_path.read_text(encoding="utf-8") == before

    # A strand's dispatch arrives assigned and is never proposed for.
    assigned_path = protocol.create_event(inbox, "spawn", "the loom", topic="the-loom")
    assigned = protocol._read_event(assigned_path)
    prepare._stamp_topic_proposal(assigned, Run.from_event(assigned), ctx, brr_dir, "")
    assert "topic_proposed" not in protocol._read_event(assigned_path)


# ── the run's `.topic` ───────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("the-loom\n", ("slug", "the-loom")),
    ("topic: the-loom\nnarration\n", ("slug", "the-loom")),
    ("new the-post\n", ("new", "the-post")),
    ("`null`\n", ("null", "")),
    ("None\n", ("null", "")),
    ("two words\n", None),
    ("Not_A_Slug\n", None),
    ("\n\n", None),
])
def test_the_control_grammar(tmp_path, text, expected):
    (tmp_path / ".topic").write_text(text, encoding="utf-8")
    control = run_topic.read_control(tmp_path)
    assert (None if control is None else (control.op, control.slug)) == expected


def test_the_control_name_is_the_ledger_constant():
    from brr import run_ledger

    assert run_topic.CONTROL_NAME == run_ledger.RUN_TOPIC_CONTROL_NAME
    assert run_topic.CONTROL_NAME in hooks._CONTROL_FILES
    assert run_topic.CONTROL_NAME in daemon._discover_control_file_names().values() or (
        run_topic.CONTROL_NAME in set(daemon._discover_control_file_names())
    )


def _run_with_event(tmp_path: Path, **meta) -> tuple[Run, Path, Path, Path]:
    home = tmp_path / "home"
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    event_path = protocol.create_event(inbox, "telegram", "hello", **meta)
    task = Run.from_event(protocol._read_event(event_path))
    task.conversation_key = "telegram:42:"
    return task, home, inbox, outbox


def test_settle_confirms_the_waking_event_once_and_never_reclassifies(tmp_path):
    task, home, inbox, outbox = _run_with_event(tmp_path, topic_proposed="the-post")
    _topic(home, "the-loom")
    _topic(home, "the-post")
    notices: list[tuple[str, str]] = []
    say = lambda kind, text: notices.append((kind, text))  # noqa: E731

    assert run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox, notice=say) is None
    (outbox / ".topic").write_text("the-loom\n", encoding="utf-8")
    assert run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox, notice=say) == "the-loom"
    event = protocol._read_event(inbox / f"{task.event_id}.md")
    assert event["topic"] == "the-loom" and event["topic_proposed"] == "the-post"
    assert [r["ref"] for r in heddles.index(home, "the-loom")] == [task.event_id]
    assert heddles.thread_topic(home, "telegram:42:") == "the-loom"
    # idempotent
    assert run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox, notice=say) == "the-loom"
    assert len(heddles.index(home, "the-loom")) == 1
    # an override later moves the run, never the event
    (outbox / ".topic").write_text("the-post\n", encoding="utf-8")
    assert run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox, notice=say) == "the-post"
    assert protocol._read_event(inbox / f"{task.event_id}.md")["topic"] == "the-loom"
    assert heddles.index(home, "the-post") == []
    assert notices and "the waking event stays the-loom" in notices[-1][1]


def test_settle_refuses_an_unknown_slug_and_a_strand_minting(tmp_path):
    task, home, inbox, outbox = _run_with_event(tmp_path)
    _topic(home, "the-loom")
    notices: list[tuple[str, str]] = []
    (outbox / ".topic").write_text("the-lom\n", encoding="utf-8")
    assert run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox,
                            notice=lambda k, t: notices.append((k, t))) is None
    assert notices[-1][0] == "refused" and "`new the-lom`" in notices[-1][1]
    (outbox / ".topic").write_text("new the-post\n", encoding="utf-8")
    assert run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox,
                            notice=lambda k, t: notices.append((k, t)), is_strand=True) is None
    assert notices[-1][0] == "refused" and "strand" in notices[-1][1]
    assert not (home / "surface" / "topics" / "the-post.md").exists()


def test_a_run_that_errs_with_no_topic_is_marked_and_null_is_a_decision(tmp_path):
    absent, _home, _inbox, outbox = _run_with_event(tmp_path / "a")
    assert run_topic.mark_unset_on_error(absent, outbox)
    assert absent.meta["topic"] is None and absent.meta["topic_unset"] is True

    present, home, inbox, outbox = _run_with_event(tmp_path / "b")
    _topic(home, "the-loom")
    (outbox / ".topic").write_text("the-loom\n", encoding="utf-8")
    run_topic.settle(present, outbox_dir=outbox, account_home=home, inbox_dir=inbox)
    assert not run_topic.mark_unset_on_error(present, outbox)
    assert "topic_unset" not in present.meta

    null, home, inbox, outbox = _run_with_event(tmp_path / "c")
    (outbox / ".topic").write_text("null\n", encoding="utf-8")
    run_topic.settle(null, outbox_dir=outbox, account_home=home, inbox_dir=inbox)
    assert not run_topic.mark_unset_on_error(null, outbox)

    strand, _home, _inbox, outbox = _run_with_event(tmp_path / "d", topic="the-loom")
    assert not run_topic.mark_unset_on_error(strand, outbox)


def test_the_next_run_on_the_thread_sees_the_unset_predecessor_and_may_assign_it(tmp_path, monkeypatch):
    from brr.outbox import topic as topic_verb
    from brr.outbox.shapes import DrainContext, OutboxFile

    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    inbox = brr_dir / "inbox"
    key = "telegram:42:"
    failed_event = protocol.create_event(inbox, "telegram", "first", status="done")
    failed = Run(id="run-260915-0300-aaaa", event_id=failed_event.stem, body="", source="telegram",
                 status="error", conversation_key=key, meta={"topic": None, "topic_unset": True})
    failed.save(runs_dir)
    protocol.update_event_meta(protocol._read_event(failed_event), run_id=failed.id)
    conversations.append_run(brr_dir, key, run_id=failed.id, event_id=failed.event_id,
                             env="worktree", status="error")
    conversations.append_run(brr_dir, key, run_id="run-260915-0310-bbbb", event_id="evt-next",
                             env="worktree", status="running")
    assert run_topic.predecessor_topic_unset(brr_dir, key, "run-260915-0310-bbbb") == {
        "run": failed.id, "event": failed.event_id,
    }
    from brr import prompts

    lines = prompts._topic_bundle_lines({"predecessor_topic_unset": f"{failed.id} {failed.event_id}"})
    assert lines[-1].startswith(f"- predecessor_topic_unset: {failed.id} ended in error")
    assert f"`topic: assign <slug> -> {failed.event_id}`" in lines[-1]

    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = account.resolve_context(repo, {"home.path": str(tmp_path / "home"), "repo.label": "o/r"})
    home = account.context_home_root(ctx)
    _topic(home, "the-loom")
    outbox = brr_dir / "outbox" / "evt-next"
    outbox.mkdir(parents=True)

    def drive(value: str):
        staged = outbox / "assign.md"
        staged.write_text(f"---\ntopic: {value}\n---\n", encoding="utf-8")
        f = OutboxFile(
            path=staged, frontmatter={"topic": value}, body="",
            run=Run(id="run-260915-0310-bbbb", event_id="evt-next", body="", source="telegram"),
            ctx=DrainContext(
                emit=daemon._WorkerEmit(brr_dir=brr_dir, conversation_key=key, event_id="evt-next"),
                responses_dir=brr_dir / "responses", event_id="evt-next", outbox_dir=outbox,
                inbox_dir=inbox, repo_root=repo, account_context=ctx, stats=None, address_sources=None,
            ),
        )
        monkeypatch.setattr(daemon.updates, "emit", lambda *_a, **_k: None)
        return topic_verb.handle(f)

    assert drive(f"assign the-loom -> {failed.event_id}").outcome == "accepted"
    reread = Run.from_file(run_manifest_path(runs_dir, failed.id))
    assert reread.meta["topic"] == "the-loom" and "topic_unset" not in reread.meta
    assert protocol._read_event(failed_event)["topic"] == "the-loom"
    assert [r["ref"] for r in heddles.index(home, "the-loom")] == [failed.event_id]
    assert run_topic.predecessor_topic_unset(brr_dir, key, "run-260915-0310-bbbb") is None
    # once: the second assignment is a reclassification and is refused
    drive(f"assign the-loom -> {failed.id}")
    last = daemon._read_outbox_notices(outbox)[-1]
    assert last["kind"] == "refused" and "is not topic-unset" in last["text"]


def test_the_bundle_reads_the_event_topic():
    from brr import prompts

    assert prompts._topic_bundle_lines(None) == []
    (assigned,) = prompts._topic_bundle_lines({"topic": "the-loom", "topic_proposed": "x"})
    assert "`the-loom` — assigned to this event at dispatch" in assigned
    (proposed,) = prompts._topic_bundle_lines({"topic_proposed": "the-post", "topic_proposed_by": "thread"})
    assert proposed.startswith("- Topic: `the-post` proposed (thread) — this run's topic: an existing heddle")
    (none,) = prompts._topic_bundle_lines({})
    assert none.startswith("- Topic: none proposed")


def test_the_boot_asks_for_the_topic_beside_name_and_mood(tmp_path):
    from brr import prompts
    from brr.bootscore import format_kernel

    kernel = format_kernel(prompts.build_boot_score(
        tmp_path, is_daemon=True, environment="host", event_ids=("evt-1",), has_event_body=True,
    ))
    assert "first outward ⇒ .name + .mood + .topic" in kernel
    assert "this run's topic: an existing heddle ∨ `new <slug>` ∨ null" in kernel


# ── every verb carries it: the ones the goldens do not drive ──────────


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return env


def _git_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    for argv in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "one"]):
        subprocess.run(argv, cwd=root, env=_git_env(), check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, env=_git_env(), check=True,
                          capture_output=True, text=True).stdout.strip()


def _seat(tmp_path: Path, monkeypatch, files: dict[str, str], *, control: str | None = "the-loom",
          git: bool = True):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "look", status="processing",
                                telegram_user_id="42", telegram_chat_id="42")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    repo = tmp_path / "repo"
    head = _git_repo(repo) if git else ""
    if not git:
        repo.mkdir()
    ctx = account.resolve_context(repo, {"home.path": str(tmp_path / "home"), "repo.label": "o/r"})
    home = account.context_home_root(ctx)
    _topic(home, "the-loom")
    _topic(home, "the-post")
    if control is not None:
        (outbox / ".topic").write_text(control + "\n", encoding="utf-8")
    for name, text in files.items():
        (outbox / name).write_text(text, encoding="utf-8")
    emitted: list = []
    monkeypatch.setattr(daemon.updates, "emit", lambda _b, pkt: emitted.append(pkt))
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram")
    task = Run(id="run-seat", event_id=own.stem, body="look", source="telegram",
               meta={"repo_label": "o/r"})
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    stats: dict[str, int] = {}
    promoted = daemon._drain_outbox(emit, task, responses, own.stem, outbox, inbox,
                                    repo_root=repo, account_context=ctx, stats=stats)
    return {"promoted": promoted, "home": home, "inbox": inbox, "outbox": outbox, "task": task,
            "head": head, "brr_dir": brr_dir, "notices": daemon._read_outbox_notices(outbox)}


def test_fold_carries_the_topic_on_the_bench_file_the_ask_and_the_index(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {"fold.md": "---\nfold: src/brr\ntopic: the-post\n---\n"})
    bench = got["home"] / "bench" / "o__r" / "src" / "brr" / f"{got['head']}.md"
    assert "topic: the-post\n" in bench.read_text(encoding="utf-8")
    (ask,) = [protocol._read_event(p) for p in got["inbox"].glob("*.md")
              if protocol._read_event(p).get("source") == "fold"]
    assert ask["topic"] == "the-post"
    assert heddles.index(got["home"], "the-post") == [{
        "kind": "fold", "ref": f"bench/o__r/src/brr/{got['head']}.md",
        "at": heddles.index(got["home"], "the-post")[0]["at"], "run": "run-seat",
    }]
    # the run's own `.topic` still confirmed the waking event
    assert [r["kind"] for r in heddles.index(got["home"], "the-loom")] == ["event"]


def test_fold_without_a_topic_writes_the_bench_file_it_always_did(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {"fold.md": "---\nfold: src\n---\n"}, control=None)
    bench = got["home"] / "bench" / "o__r" / "src" / f"{got['head']}.md"
    assert "topic:" not in bench.read_text(encoding="utf-8")
    assert not list((got["home"] / "surface" / "topics").glob("*.index.jsonl"))


class _GreenGitHub:
    def __init__(self):
        self.state = "OPEN"

    def pr(self, number):
        data = {"number": number, "state": self.state, "headRefOid": "a" * 40, "baseRefName": "main",
                "title": "t", "url": "https://github.com/o/r/pull/1", "files": []}
        if self.state == "MERGED":
            data["mergeCommit"] = {"oid": "b" * 40}
        return data

    def checks(self, number):
        return [land.Check("Backend tests", "SUCCESS")]

    def merge(self, number, *, head_sha):
        self.state = "MERGED"


class _Checkout:
    def fetch(self, base): ...
    def contains(self, sha, ref): return True
    def current_branch(self): return "feature"
    def dirty_paths(self): return set()
    def fast_forward(self, branch, source_ref): return False, "no"
    def head(self): return "c" * 40


def test_land_carries_the_topic_on_the_produce_row_and_the_announcement(tmp_path, monkeypatch):
    monkeypatch.setattr(land.seams, "github", lambda _root, _label: _GreenGitHub())
    monkeypatch.setattr(land.seams, "checkout", lambda _root: _Checkout())
    monkeypatch.setattr(land.seams, "live_strands", lambda _task, _brr: [])
    got = _seat(tmp_path, monkeypatch, {"land.md": "---\nland: 7\ntopic: the-post\n---\n"}, git=False)
    (row,) = _jsonl(got["brr_dir"] / "runs" / "run-seat" / "produce.jsonl")
    assert row["topic"] == "the-post" and row["ref"] == "b" * 40
    kinds = [(r["kind"], r["ref"]) for r in heddles.index(got["home"], "the-post")]
    assert ("produce", "b" * 40) in kinds
    # the announcement is a message of the same act
    assert any(k == "message" for k, _ in kinds)


def test_a_reply_with_no_topic_anywhere_carries_none(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {"reply.md": "plain\n"}, control=None)
    (message,) = (got["home"] / "runs").rglob("messages/*.md")
    assert "topic:" not in message.read_text(encoding="utf-8")
    assert "topic" not in protocol._read_event(got["inbox"] / f"{got['task'].event_id}.md")


def test_a_waking_event_topic_is_inherited_when_the_run_has_no_control(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    got = _seat(tmp_path, monkeypatch, {}, control=None)
    # A strand-shaped run: its waking event arrived assigned by its parent.
    task = got["task"]
    task.meta["topic"] = "the-post"
    (got["outbox"] / "reply.md").write_text("from the strand\n", encoding="utf-8")
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=task.event_id)
    daemon._drain_outbox(emit, task, brr_dir / "responses", task.event_id, got["outbox"], got["inbox"],
                         repo_root=tmp_path / "repo", account_context=account.resolve_context(
                             tmp_path / "repo", {"home.path": str(tmp_path / "home"), "repo.label": "o/r"}),
                         stats={})
    (message,) = (got["home"] / "runs").rglob("messages/*.md")
    assert "topic: the-post" in message.read_text(encoding="utf-8")


def test_topic_show_renders_the_index_into_the_bench_and_asks_the_seat(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {"reply.md": "the first line\nand more\n"})
    home = got["home"]
    strand = protocol.create_event(got["inbox"], "spawn", "a strand body", title="the child",
                                   status="done", run_id="run-child")
    Run(id="run-child", event_id=strand.stem, body="", source="spawn", status="done").save(
        got["brr_dir"] / "runs")
    heddles.append_index(home, "the-loom", kind="strand", ref=strand.stem, run="run-seat")
    heddles.append_index(home, "the-loom", kind="produce", ref="d" * 40, run="run-seat")
    task = got["task"]
    # move 5d: every kind, heads, and the page asked for (bench defaults to false)
    (got["outbox"] / "show.md").write_text(
        "---\ntopic: show the-loom since 1d kinds: messages, strands, produce, events "
        "depth: heads bench: true\n---\n", encoding="utf-8")
    emit = daemon._WorkerEmit(brr_dir=got["brr_dir"], conversation_key="telegram:42:", event_id=task.event_id)
    ctx = account.resolve_context(tmp_path / "repo", {"home.path": str(tmp_path / "home"), "repo.label": "o/r"})
    assert daemon._drain_outbox(emit, task, got["brr_dir"] / "responses", task.event_id, got["outbox"],
                                got["inbox"], repo_root=tmp_path / "repo", account_context=ctx,
                                stats={}) == 1
    page = home / "bench" / "o__r" / "topics" / "the-loom" / f"{got['head']}.md"
    text = page.read_text(encoding="utf-8")
    assert text.startswith("---\nplace: topics/the-loom\ncommit: " + got["head"])
    assert "since: 1d\n" in text
    assert "4 acts since 1d: message 1 · strand 1 · produce 1 · event 1" in text
    assert "message `run-seat/000001-interim` — the first line" in text
    assert f"strand `{strand.stem}` — the child · done · run-child" in text
    assert f"produce `{'d' * 40}` — `{'d' * 10}`" in text
    assert f"event `{task.event_id}` — look" in text
    (ask,) = [protocol._read_event(p) for p in got["inbox"].glob("*.md")
              if protocol._read_event(p).get("source") == "fold"]
    assert ask["focus_place"] == "topics/the-loom" and ask["focus_bench_path"] == str(page)
    # `brnrd hud --topic` prints the same rendering
    from brr import topic_show

    printed = topic_show.render(home, "the-loom", since="1d", inbox_dirs=[got["inbox"]],
                                runs_dirs=[got["brr_dir"] / "runs"],
                                kinds=("messages", "strands", "produce", "events"), depth="heads")
    assert text.endswith(printed)


def test_topic_show_is_refused_for_a_strand_and_for_an_unknown_topic(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {"show.md": "---\ntopic: show nowhere\n---\n"}, control=None)
    assert got["notices"][-1]["kind"] == "refused" and "show nowhere" in got["notices"][-1]["text"]


# ── lit by assignment ────────────────────────────────────────────────


def test_an_assigned_act_brightens_a_heddle_exactly_like_a_match(tmp_path):
    home = tmp_path / "home"
    _topic(home, "the-post")  # an empty signature: nothing but assignments can light it
    run_dir = tmp_path / "runs" / "run-a"
    run_dir.mkdir(parents=True)
    now = time.time()
    hour_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))
    assert heddles.light(home, run_dir, now, run_id="run-a")[0].brightness == 0.0
    heddles.append_index(home, "the-post", kind="message", ref="run-a/1", at=hour_ago, run="run-a")
    heddles.append_index(home, "the-post", kind="message", ref="run-b/1", at=time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), run="run-b")  # another run's act: not this chip
    (lit,) = heddles.light(home, run_dir, now, run_id="run-a")
    assert lit.matched_by == ("assigned",)
    assert lit.brightness == pytest.approx(0.5, abs=0.01)


# ── the Shell row names its paths ────────────────────────────────────


@pytest.fixture
def _tree(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    for rel in ("src/brr/x.py", "src/brr/y.py", "docs/a.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("", encoding="utf-8")
    return root


@pytest.mark.parametrize("command,expected", [
    ("sed -i '' 's/a/b/' src/brr/x.py", ["src/brr/x.py"]),
    ("python3 - <<'PY'\np = 'src/brr/y.py'\nopen('docs/a.md')\nPY", ["src/brr/y.py"]),
    ("git show HEAD:src/brr/x.py | head -5", ["src/brr/x.py"]),
    ("ls docs", ["docs"]),
    ("grep -n foo src/brr/x.py:12 src/brr/nope.py /etc", ["src/brr/x.py"]),
])
def test_the_shell_row_reads_five_command_shapes(_tree, command, expected):
    paths = hooks._shell_place_paths({"command": command}, str(_tree), _tree)
    assert paths == [str(_tree / rel) for rel in expected]


def test_the_shell_row_caps_at_eight_and_ignores_what_is_not_there(_tree):
    for n in range(12):
        (_tree / f"f{n}.txt").write_text("", encoding="utf-8")
    command = "cat nothing.txt " + " ".join(f"f{n}.txt" for n in range(12))
    paths = hooks._shell_place_paths({"command": command}, str(_tree), _tree)
    assert paths == [str(_tree / f"f{n}.txt") for n in range(8)]
    assert hooks._shell_place_paths({"command": "echo hello world"}, str(_tree), _tree) == []
    assert hooks._shell_place_paths({"command": "ls src"}, str(_tree), None) == []


def test_record_boundary_fills_place_for_a_shell_call(_tree, tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = hooks.HookContext.__new__(hooks.HookContext)
    ctx.boot_score_path = run_dir / "boot-score.json"
    ctx.repo_dir = None
    payload = {"tool_name": "Bash", "tool_input": {"command": "sed -n 1,5p src/brr/x.py docs/a.md"},
               "cwd": str(_tree)}
    hooks.record_boundary(ctx, hooks.PHASE_POST_TOOL, {}, payload)
    (row,) = _jsonl(run_dir / hooks.BOUNDARIES_NAME)
    assert row["place"] == {
        "path": str(_tree / "src/brr/x.py"),
        "paths": [str(_tree / "src/brr/x.py"), str(_tree / "docs/a.md")],
        "commit": None,
    }
    payload = {"tool_name": "Read", "tool_input": {"file_path": str(_tree / "docs/a.md")}, "cwd": str(_tree)}
    hooks.record_boundary(ctx, hooks.PHASE_POST_TOOL, {}, payload)
    assert _jsonl(run_dir / hooks.BOUNDARIES_NAME)[-1]["place"]["paths"] == [str(_tree / "docs/a.md")]
    # and a place the shell named lights the heddle whose signature holds it
    home = tmp_path / "home"
    _topic(home, "the-docs", places=("docs/**",))
    (lit,) = heddles.light(home, run_dir, time.time(), roots=[_tree], run_id="run-x")
    assert "place" in lit.matched_by


# ── the HUD's two keys ───────────────────────────────────────────────


def test_the_hud_carries_the_run_topic_and_the_event_topic(tmp_path):
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram",
               meta={"topic_proposed": "the-post", "topic": "the-loom", "run_topic": "the-post"})
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    built = hud.build(hud.HUDInputs(outbox_dir=outbox, inbox_dir=tmp_path / "inbox",
                                    current_event_id="evt-1", task=task, phase="running",
                                    refresh_levels=False))
    payload = built.to_dict()
    assert payload["run"]["topic"] == "the-post"
    assert payload["inbound"]["current_event_topic"] == {"proposed": "the-post", "confirmed": "the-loom"}
    assert hud.HUD.from_dict(payload).inbound.current_event_topic == {
        "proposed": "the-post", "confirmed": "the-loom"}


def test_the_topic_nag_speaks_after_the_grace_and_goes_quiet_on_any_control(tmp_path):
    payload = {
        "run": {"topic": None},
        "inbound": {"current_event_topic": {"proposed": "the-loom", "confirmed": None}},
        "budget": {"elapsed_seconds": 300},
    }
    # The seed/stop prose arm, like `.name`'s nudge (#1897: the mid-run bar
    # never grew one) — `seed=True` is a resumed seat's first boundary.
    text = hooks.format_delta(payload, seed=True, outbox_dir=tmp_path) or ""
    assert "- .topic: still unwritten — this run's topic: `the-loom` proposed, or an existing heddle" in text
    (tmp_path / ".topic").write_text("null\n", encoding="utf-8")
    assert ".topic: still unwritten" not in (hooks.format_delta(payload, seed=True, outbox_dir=tmp_path) or "")
    early = dict(payload, budget={"elapsed_seconds": 30})
    (tmp_path / ".topic").unlink()
    assert ".topic: still unwritten" not in (hooks.format_delta(early, seed=True, outbox_dir=tmp_path) or "")
