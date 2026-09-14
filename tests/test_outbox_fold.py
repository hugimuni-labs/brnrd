"""`fold: <place>` — the bench file and the ask, nothing more (``brr.outbox.fold``)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from brr import account, daemon, protocol
from brr.outbox import fold
from brr.run import Run


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
    )
    return env


def _repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    for argv in (
        ["git", "init", "-q"],
        ["git", "commit", "-q", "--allow-empty", "-m", "one"],
    ):
        subprocess.run(argv, cwd=root, env=_git_env(), check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, env=_git_env(), check=True,
        capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _drive(tmp_path: Path, monkeypatch, text: str, *, meta: dict | None = None, git: bool = True):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True)
    own = protocol.create_event(
        inbox, "telegram", "look at this", status="processing",
        telegram_user_id="42", telegram_chat_id="42",
    )
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    (outbox / "fold.md").write_text(text, encoding="utf-8")
    emitted: list = []
    monkeypatch.setattr(daemon.updates, "emit", lambda _b, pkt: emitted.append(pkt))
    repo = tmp_path / "repo"
    head = _repo(repo) if git else ""
    if not git:
        repo.mkdir()
    ctx = account.resolve_context(
        repo, {"home.path": str(tmp_path / "home"), "repo.label": "hugimuni-labs/brnrd"},
    )
    task = Run(
        id="run-seat", event_id=own.stem, body="look", source="telegram",
        meta={"repo_label": "hugimuni-labs/brnrd", **(meta or {})},
    )
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    stats: dict[str, int] = {}

    def drain() -> int:
        return daemon._drain_outbox(
            emit, task, responses, own.stem, outbox, inbox,
            repo_root=repo, account_context=ctx, stats=stats,
        )

    promoted = drain()
    return {
        "promoted": promoted, "stats": stats, "head": head,
        "home": account.context_home_root(ctx), "events": _fold_events(inbox),
        "notices": daemon._read_outbox_notices(outbox),
        "emitted": emitted, "outbox": outbox, "inbox": inbox, "drain": drain,
    }


def _fold_events(inbox: Path) -> list[tuple[dict, str]]:
    out = []
    for p in sorted(inbox.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        fm = protocol.parse_frontmatter(text)
        if fm.get("source") == "fold":
            out.append((fm, protocol.frontmatter_body(text)))
    return out


def test_fold_creates_the_bench_file_and_delivers_the_ask(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch,
        "---\nfold: src/brr/daemon.py\nquestion: where does the drain dispatch?\n---\n",
    )
    head = result["head"]
    path = result["home"] / "bench" / "hugimuni-labs__brnrd" / "src" / "brr" / "daemon.py" / f"{head}.md"
    assert path.exists()
    fm = protocol.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert fm["place"] == "src/brr/daemon.py"
    assert fm["commit"] == head
    assert fm["question"] == "where does the drain dispatch?"
    assert fm["made_at"]
    assert protocol.frontmatter_body(path.read_text(encoding="utf-8")).strip() == ""

    ((event, body),) = result["events"]
    assert event["source"] == "fold"
    assert event["status"] == "pending"
    assert event["conversation_key"] == "telegram:42:"
    assert event["focus_place"] == "src/brr/daemon.py"
    assert event["focus_commit"] == head
    assert event["focus_question"] == "where does the drain dispatch?"
    assert event["focus_bench_path"] == str(path)
    assert event["fold_by_run"] == "run-seat"
    assert f"fold src/brr/daemon.py @ {head[:10]}" in body

    assert result["stats"] == {"fold": 1}
    assert result["promoted"] == 1
    assert [p.type for p in result["emitted"]] == ["fold_requested"]
    assert result["notices"] == []
    assert (result["outbox"] / ".processed" / "fold.md").exists()


def test_fold_without_a_question_and_at_the_root(tmp_path, monkeypatch):
    result = _drive(tmp_path, monkeypatch, "---\nfold: .\n---\n")
    path = result["home"] / "bench" / "hugimuni-labs__brnrd" / f"{result['head']}.md"
    assert path.exists()
    ((event, _body),) = result["events"]
    assert event["focus_place"] == "."
    assert event["focus_question"] == ""


def test_an_existing_fold_is_left_as_the_weaver_wrote_it(tmp_path, monkeypatch):
    result = _drive(tmp_path, monkeypatch, "---\nfold: docs\n---\n")
    path = result["home"] / "bench" / "hugimuni-labs__brnrd" / "docs" / f"{result['head']}.md"
    path.write_text("---\nplace: docs\n---\nthe weaver's body\n", encoding="utf-8")

    (result["outbox"] / "fold-again.md").write_text("---\nfold: docs\n---\n", encoding="utf-8")
    assert result["drain"]() == 1

    assert path.read_text(encoding="utf-8").endswith("the weaver's body\n")
    events = _fold_events(result["inbox"])
    assert len(events) == 2
    assert "already there — left as it was" in events[-1][1]


@pytest.mark.parametrize("place", ["/etc/passwd", "../outside", "src/../../x", "C:/x"])
def test_a_place_outside_the_repo_is_refused(tmp_path, monkeypatch, place):
    result = _drive(tmp_path, monkeypatch, f"---\nfold: {place}\n---\n")
    assert result["events"] == []
    (notice,) = result["notices"]
    assert notice["kind"] == "refused"
    assert (notice["source_file"], notice["verb"]) == ("fold.md", "fold")
    assert not (result["home"] / "bench").exists()


def test_a_strand_may_not_fold(tmp_path, monkeypatch):
    result = _drive(tmp_path, monkeypatch, "---\nfold: src\n---\n", meta={"strand": True})
    assert result["events"] == []
    assert "strand cannot read" in result["notices"][0]["text"]


def test_no_readable_head_is_dropped(tmp_path, monkeypatch):
    result = _drive(tmp_path, monkeypatch, "---\nfold: src\n---\n", git=False)
    assert result["events"] == []
    (notice,) = result["notices"]
    assert notice["kind"] == "dropped"
    assert "rev-parse HEAD" in notice["text"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/brr", "src/brr"), ("./src//brr/", "src/brr"), (".", "."),
        ("", None), ("..", None), ("a/../b", None), ("/abs", None),
    ],
)
def test_normalise_place(raw, expected):
    assert fold.normalise_place(raw) == expected


def test_fold_is_a_declared_internal_source():
    assert fold.SOURCE in protocol.INTERNAL_SOURCES
