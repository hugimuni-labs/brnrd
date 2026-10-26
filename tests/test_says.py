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
