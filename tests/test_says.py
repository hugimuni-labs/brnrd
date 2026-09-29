"""A say's words kept at home (brr/says.py) — his 2026-09-29 steer: shown, never stored on the server."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from brr import asks, says

EVT = "evt-1790354596107625000-vz0z"
EVT2 = "evt-1790449929122436000-mrqh"


def _store(repo: Path, event_id: str, body: str) -> None:
    thread = repo / ".brr" / "conversations" / "cloud__telegram__1__"
    thread.mkdir(parents=True, exist_ok=True)
    rows = [
        {"kind": "event", "event_id": event_id, "body": body, "ts": "2026-09-25T16:43:16Z",
         "conversation_key": "cloud:telegram:1:", "correspondent_key": "telegram:user-id:1",
         "source": "cloud"},
        {"kind": "response", "event_id": event_id, "body": "the reply, not the say"},
    ]
    (thread / f"{event_id}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    warp = home / "surface" / "warp"
    warp.mkdir(parents=True)
    (warp / "w-1.md").write_text("# One\n\nsays: \n")
    (warp / "w-2.md").write_text("# Two\n\nsays: \n")
    return warp


def test_write_say_copies_the_message_once_and_grows_items(tmp_path):
    warp, repo = _home(tmp_path), tmp_path / "repo"
    _store(repo, EVT, "the warp body is just nowhere")
    assert says.write_say(warp, EVT, [repo], item_id="w-1")
    text = (warp / "says" / f"{EVT}.md").read_text()
    assert "the warp body is just nowhere" in text
    assert "the reply, not the say" not in text
    assert "items: w-1" in text
    # a second item stamping the same message: one file, two items, a part named
    assert says.write_say(warp, EVT, [], item_id="w-2", part="just nowhere")
    text = (warp / "says" / f"{EVT}.md").read_text()
    assert "items: w-1 w-2" in text
    assert "part w-2: just nowhere" in text
    assert text.count("the warp body is just nowhere") == 1
    assert not says.write_say(warp, EVT, [], item_id="w-2", part="just nowhere")  # idempotent


def test_write_say_without_a_store_writes_nothing(tmp_path):
    warp = _home(tmp_path)
    assert not says.write_say(warp, EVT, [tmp_path / "nowhere"], item_id="w-1")
    assert not says.write_say(warp, "evt-abc", [tmp_path], item_id="w-1")  # not an event id
    assert not (warp / "says").exists()


def test_index_carries_ids_and_home_never_words(tmp_path):
    warp, repo = _home(tmp_path), tmp_path / "repo"
    home = warp.parent.parent
    subprocess.run(["git", "init", "-q", "-b", "main", str(home)], check=True)
    subprocess.run(["git", "-C", str(home), "remote", "add", "origin",
                    "git@github.com:acme/acme-home.git"], check=True)
    _store(repo, EVT, "secret words")
    says.write_say(warp, EVT, [repo], item_id="w-1")
    index = (warp / "says" / "index.md").read_text()
    assert "secret words" not in index
    parsed = says.parse_index(index)
    assert parsed["home"] == "https://github.com/acme/acme-home"
    assert parsed["says"] == {EVT}
    assert says.say_url(parsed, EVT) == (
        f"https://github.com/acme/acme-home/blob/main/surface/warp/says/{EVT}.md"
    )
    assert says.say_url(parsed, EVT2) is None  # not kept at home ⇒ no link


def test_only_the_index_is_mirrored():
    assert says.is_mirrored("surface/warp/w-1.md")
    assert says.is_mirrored("surface/warp/says/index.md")
    assert not says.is_mirrored(f"surface/warp/says/{EVT}.md")


def test_corpus_files_leave_the_words_at_home(tmp_path, monkeypatch):
    from brr import account

    warp, repo = _home(tmp_path), tmp_path / "repo"
    _store(repo, EVT, "secret words")
    says.write_say(warp, EVT, [repo], item_id="w-1")
    home = warp.parent.parent
    monkeypatch.setattr(account, "context_home_root", lambda ctx: home)
    monkeypatch.setattr(account, "work_surface_path", lambda ctx: home / "surface")
    monkeypatch.setattr(account, "knowledge_path", lambda ctx: home / "knowledge")
    paths = {f.path for f in account.corpus_files(object())}
    assert "surface/warp/w-1.md" in paths
    assert "surface/warp/says/index.md" in paths
    assert f"surface/warp/says/{EVT}.md" not in paths


def test_build_asks_links_a_say_kept_at_home():
    index = f"home: https://github.com/acme/acme-home\nbranch: main\nsays: {EVT}\n"
    files = [
        ("surface/warp/w-1.md", f"# One\n\nsays: {EVT} {EVT2}\n"),
        ("surface/warp/says/index.md", index),
    ]
    payload = asks.build_asks(files)
    (row,) = payload["asks"]
    by_event = {s["event"]: s for s in row["says"]}
    assert by_event[EVT]["url"].endswith(f"/surface/warp/says/{EVT}.md")
    assert by_event[EVT2]["url"] is None
    assert [r["id"] for r in payload["asks"]] == ["w-1"]  # the index is never an item


def test_stamp_say_keeps_the_message_and_backfill_fills_the_rest(tmp_path):
    warp, repo = _home(tmp_path), tmp_path / "repo"
    _store(repo, EVT, "first")
    _store(repo, EVT2, "second")
    asks.stamp_say(warp, "w-1", EVT, repo_roots=[repo])
    assert (warp / "says" / f"{EVT}.md").is_file()
    asks.stamp_say(warp, "w-2", EVT2)  # no roots: the row lands, the file waits
    assert not (warp / "says" / f"{EVT2}.md").exists()
    counts = says.backfill(warp, [repo])
    assert counts == {"written": 1, "present": 1, "missing": 0}
    assert "items: w-2" in (warp / "says" / f"{EVT2}.md").read_text()


def test_receipts_find_their_repo_among_several():
    """Measured 2026-09-29: two connected repos, 0 of 41 receipts linked."""
    bases = {
        "hugimuni-labs/brnrd": {"forge": "https://github.com/hugimuni-labs/brnrd", "forge_kind": "github",
                                "kb": "https://kb.example/brnrd/"},
        "hugimuni-labs/hugimuni": {"forge": "https://github.com/hugimuni-labs/hugimuni", "forge_kind": "github",
                                   "kb": "https://kb.example/hugimuni/"},
    }
    only_brnrd = asks.run_repo_slugs(["runs/hugimuni-labs__brnrd/run-260925-0424-tfzb/state.md"])
    row = {"return": "#2088 · design-the-ask.md · w-87", "receipt": None, "attempts": []}
    hint = asks.repo_hint(row, bases, only_brnrd)
    assert hint == "hugimuni-labs/brnrd"  # every mirrored run is brnrd's
    by_ref = {r["ref"]: r["url"] for r in asks.resolve_receipts(row, bases, hint=hint)}
    assert by_ref["#2088"] == "https://github.com/hugimuni-labs/brnrd/pull/2088"
    assert by_ref["design-the-ask.md"] == "https://kb.example/brnrd/design-the-ask.md"
    assert by_ref["w-87"] == "/warp/w-87"

    both = asks.run_repo_slugs([
        "runs/hugimuni-labs__brnrd/run-260925-0424-tfzb/state.md",
        "runs/hugimuni-labs__hugimuni/run-260926-2222-adds/state.md",
    ])
    assert asks.repo_hint(row, bases, both) is None  # two repos, no attempt: no guess
    row_on_hugimuni = {**row, "attempts": ["run-260926-2222-adds"]}
    assert asks.repo_hint(row_on_hugimuni, bases, both) == "hugimuni-labs/hugimuni"
    unlinked = {r["ref"]: r["url"] for r in asks.resolve_receipts(row, bases, hint=None)}
    assert unlinked["#2088"] is None
