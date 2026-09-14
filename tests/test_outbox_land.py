"""`land: <pr>` against a fake GitHub and a fake checkout — ``brr.outbox.land``.

The shapes this verb exists to close: #1969 merged with ``Backend tests ·
pending`` on screen (a grepped status), and #1973's receipt was composed
before the checkout was read. Every test below asserts on a value the fake
*served*, and that nothing past a refusal was attempted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import daemon, protocol
from brr.outbox import land
from brr.run import Run

HEAD = "a" * 40
MERGED = "b" * 40
BEFORE = "c" * 40


class FakeGitHub:
    def __init__(self, checks, *, state="OPEN", base="main", merge_error=None,
                 merged=True, files=("src/brr/daemon.py",)):
        self._checks = [land.Check(n, s) for n, s in checks]
        self.state = state
        self.base = base
        self.merge_error = merge_error
        self.merged = merged
        self.files = files
        self.calls: list[tuple] = []

    def pr(self, number):
        self.calls.append(("pr", number))
        data = {
            "number": number, "state": self.state, "headRefOid": HEAD,
            "baseRefName": self.base, "title": "The outbox is a verb table",
            "url": f"https://github.com/o/r/pull/{number}",
            "files": [{"path": p} for p in self.files],
        }
        if self.state == "MERGED":
            data["mergeCommit"] = {"oid": MERGED}
        return data

    def checks(self, number):
        self.calls.append(("checks", number))
        return list(self._checks)

    def merge(self, number, *, head_sha):
        self.calls.append(("merge", number, head_sha))
        if self.merged:
            self.state = "MERGED"
        if self.merge_error:
            raise land.LandError(self.merge_error)


class FakeCheckout:
    def __init__(self, *, branch="main", dirty=(), on_origin=True, ff_ok=True):
        self.branch = branch
        self.dirty = set(dirty)
        self.on_origin = on_origin
        self.ff_ok = ff_ok
        self.sha = BEFORE
        self.calls: list[tuple] = []

    def fetch(self, base):
        self.calls.append(("fetch", base))

    def contains(self, sha, ref):
        self.calls.append(("contains", sha, ref))
        if ref == "HEAD":
            return self.sha == MERGED
        return self.on_origin

    def current_branch(self):
        return self.branch

    def dirty_paths(self):
        return set(self.dirty)

    def fast_forward(self, branch, source_ref):
        self.calls.append(("fast_forward", branch, source_ref))
        if self.ff_ok:
            self.sha = MERGED
            return True, ""
        return False, "not a fast-forward"

    def head(self):
        return self.sha


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _drive(tmp_path: Path, monkeypatch, github, checkout, *, strands=(), meta=None,
           text="---\nland: 1974\n---\n"):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True)
    own = protocol.create_event(
        inbox, "telegram", "land it", status="processing",
        telegram_user_id="42", telegram_chat_id="42",
    )
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    (outbox / "land.md").write_text(text, encoding="utf-8")
    emitted: list = []
    monkeypatch.setattr(daemon.updates, "emit", lambda _b, pkt: emitted.append(pkt))
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram")
    monkeypatch.setattr(land.seams, "github", lambda _root, _label: github)
    monkeypatch.setattr(land.seams, "checkout", lambda _root: checkout)
    monkeypatch.setattr(land.seams, "live_strands", lambda _task, _brr: list(strands))
    task = Run(
        id="run-seat", event_id=own.stem, body="land it", source="telegram",
        meta={"repo_label": "hugimuni-labs/brnrd", **(meta or {})},
    )
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    stats: dict[str, int] = {}
    repo = tmp_path / "repo"
    repo.mkdir()
    promoted = daemon._drain_outbox(
        emit, task, responses, own.stem, outbox, inbox, repo_root=repo, stats=stats,
    )
    partials = sorted((responses).rglob("*")) if responses.exists() else []
    replies = [p.read_text(encoding="utf-8") for p in partials if p.is_file() and p.suffix == ".md"]
    return {
        "promoted": promoted,
        "stats": stats,
        "notices": daemon._read_outbox_notices(outbox),
        "replies": replies,
        "produce": _read_jsonl(brr_dir / "runs" / "run-seat" / land.PRODUCE_NAME),
        "relics": _read_jsonl(outbox / ".relics.jsonl"),
        "emitted": [p.type for p in emitted],
        "outbox": outbox,
    }


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _reply_text(result) -> str:
    return "\n".join(result["replies"])


# ── green ──────────────────────────────────────────────────────────────


def test_green_checks_merge_read_back_fast_forward_produce_and_announce(tmp_path, monkeypatch):
    github = FakeGitHub([("Backend tests", "SUCCESS"), ("Link liveness", "SKIPPED")])
    checkout = FakeCheckout()
    result = _drive(tmp_path, monkeypatch, github, checkout)

    assert github.calls == [
        ("pr", 1974), ("checks", 1974), ("merge", 1974, HEAD), ("pr", 1974),
    ]
    assert ("fetch", "main") in checkout.calls
    assert ("contains", MERGED, "origin/main") in checkout.calls
    assert ("fast_forward", "main", "origin/main") in checkout.calls

    assert result["produce"] == [{
        "at": result["produce"][0]["at"], "base": "main", "by": "frame", "head": HEAD,
        "kind": "knot", "pr": 1974, "ref": MERGED, "verb": "land",
    }]
    (relic,) = result["relics"]
    assert (relic["kind"], relic["sha"], relic["pr"]) == ("merge", MERGED, 1974)

    reply = _reply_text(result)
    assert f"Merged: #1974 → {MERGED[:10]}" in reply
    assert "Backend tests ✓" in reply and "Link liveness (skipped)" in reply
    assert f"main {BEFORE[:10]} → {MERGED[:10]}" in reply
    assert result["stats"]["land"] == 1
    assert result["stats"]["delivered"] == 1
    assert result["promoted"] == 2  # the land and its announcement

    (advisory,) = result["notices"]
    assert advisory["kind"] == "advisory"
    assert MERGED in advisory["text"]
    assert (advisory["source_file"], advisory["verb"]) == ("land.md", "land")


def test_hash_prefixed_pr_number_parses(tmp_path, monkeypatch):
    github = FakeGitHub([("ci", "SUCCESS")])
    result = _drive(tmp_path, monkeypatch, github, FakeCheckout(), text="---\nland: #1974\n---\n")
    assert ("merge", 1974, HEAD) in github.calls
    assert result["produce"][0]["pr"] == 1974


# ── not green ──────────────────────────────────────────────────────────


def test_one_red_check_refuses_naming_it_and_merges_nothing(tmp_path, monkeypatch):
    github = FakeGitHub([
        ("Backend tests", "FAILURE"), ("Frontend tests", "SUCCESS"), ("F821", "SUCCESS"),
    ])
    checkout = FakeCheckout()
    result = _drive(tmp_path, monkeypatch, github, checkout)
    assert not any(c[0] == "merge" for c in github.calls)
    assert checkout.calls == []
    (notice,) = result["notices"]
    assert notice["kind"] == "refused"
    assert "1 of 3 checks not green: Backend tests: FAILURE" in notice["text"]
    assert "Frontend tests" not in notice["text"]
    assert result["produce"] == [] and result["relics"] == [] and result["replies"] == []
    assert result["promoted"] == 0


def test_a_pending_check_refuses_the_1969_shape(tmp_path, monkeypatch):
    github = FakeGitHub([
        ("Undefined names (F821)", "SUCCESS"), ("Backend tests", "PENDING"),
        ("npm launcher package", "IN_PROGRESS"),
    ])
    result = _drive(tmp_path, monkeypatch, github, FakeCheckout())
    assert not any(c[0] == "merge" for c in github.calls)
    (notice,) = result["notices"]
    assert "Backend tests: PENDING" in notice["text"]
    assert "npm launcher package: IN_PROGRESS" in notice["text"]
    assert "2 of 3" in notice["text"]


def test_no_checks_at_all_is_not_green(tmp_path, monkeypatch):
    github = FakeGitHub([])
    result = _drive(tmp_path, monkeypatch, github, FakeCheckout())
    assert not any(c[0] == "merge" for c in github.calls)
    assert "no checks reported" in result["notices"][0]["text"]


def test_a_pr_that_is_not_open_is_refused_before_reading_checks(tmp_path, monkeypatch):
    github = FakeGitHub([("ci", "SUCCESS")], state="CLOSED")
    result = _drive(tmp_path, monkeypatch, github, FakeCheckout())
    assert github.calls == [("pr", 1974)]
    assert "is CLOSED, not OPEN" in result["notices"][0]["text"]


# ── the checkout ───────────────────────────────────────────────────────


def test_checkout_not_on_main_merges_but_does_not_fast_forward(tmp_path, monkeypatch):
    github = FakeGitHub([("ci", "SUCCESS")])
    checkout = FakeCheckout(branch="brr/some-branch")
    result = _drive(tmp_path, monkeypatch, github, checkout)
    assert ("merge", 1974, HEAD) in github.calls
    assert not any(c[0] == "fast_forward" for c in checkout.calls)
    reply = _reply_text(result)
    assert "not fast-forwarded — the checkout is on brr/some-branch, not main" in reply
    assert result["produce"][0]["ref"] == MERGED


def test_a_dirty_checkout_is_not_fast_forwarded(tmp_path, monkeypatch):
    checkout = FakeCheckout(dirty={"src/brr/x.py"})
    result = _drive(tmp_path, monkeypatch, FakeGitHub([("ci", "SUCCESS")]), checkout)
    assert not any(c[0] == "fast_forward" for c in checkout.calls)
    assert "1 uncommitted path(s)" in _reply_text(result)


def test_a_live_strand_on_the_same_files_blocks_the_fast_forward(tmp_path, monkeypatch):
    checkout = FakeCheckout()
    result = _drive(
        tmp_path, monkeypatch, FakeGitHub([("ci", "SUCCESS")]), checkout,
        strands=[("move 5", {"src/brr/daemon.py", "tests/x.py"})],
    )
    assert not any(c[0] == "fast_forward" for c in checkout.calls)
    assert "live strand move 5 touches src/brr/daemon.py" in _reply_text(result)


def test_a_live_strand_on_other_files_does_not_block(tmp_path, monkeypatch):
    checkout = FakeCheckout()
    result = _drive(
        tmp_path, monkeypatch, FakeGitHub([("ci", "SUCCESS")]), checkout,
        strands=[("docs", {"docs/a.md"})],
    )
    assert ("fast_forward", "main", "origin/main") in checkout.calls
    assert f"main {BEFORE[:10]} → {MERGED[:10]}" in _reply_text(result)


def test_an_unreadable_live_strand_counts_as_touching(tmp_path, monkeypatch):
    checkout = FakeCheckout()
    result = _drive(
        tmp_path, monkeypatch, FakeGitHub([("ci", "SUCCESS")]), checkout,
        strands=[("ghost", None)],
    )
    assert not any(c[0] == "fast_forward" for c in checkout.calls)
    assert "live strand ghost's files could not be read" in _reply_text(result)


def test_merge_not_on_origin_main_is_dropped_without_announcement(tmp_path, monkeypatch):
    checkout = FakeCheckout(on_origin=False)
    result = _drive(tmp_path, monkeypatch, FakeGitHub([("ci", "SUCCESS")]), checkout)
    (notice,) = result["notices"]
    assert notice["kind"] == "dropped"
    assert f"merged on GitHub as {MERGED[:10]}, but origin/main does not contain it" in notice["text"]
    assert result["replies"] == [] and result["produce"] == []
    assert not any(c[0] == "fast_forward" for c in checkout.calls)


def test_a_merge_error_that_did_not_merge_is_refused_with_the_read_state(tmp_path, monkeypatch):
    github = FakeGitHub([("ci", "SUCCESS")], merge_error="Head branch was modified", merged=False)
    result = _drive(tmp_path, monkeypatch, github, FakeCheckout())
    (notice,) = result["notices"]
    assert "merge failed: Head branch was modified" in notice["text"]
    assert "read back state=OPEN" in notice["text"]
    assert result["produce"] == []


def test_a_timed_out_merge_that_did_merge_is_read_back_and_landed(tmp_path, monkeypatch):
    github = FakeGitHub([("ci", "SUCCESS")], merge_error="did not answer", merged=True)
    result = _drive(tmp_path, monkeypatch, github, FakeCheckout())
    assert result["produce"][0]["ref"] == MERGED
    assert "Merged: #1974" in _reply_text(result)


# ── who may land ───────────────────────────────────────────────────────


def test_a_strand_may_not_land(tmp_path, monkeypatch):
    github = FakeGitHub([("ci", "SUCCESS")])
    result = _drive(tmp_path, monkeypatch, github, FakeCheckout(), meta={"strand": True})
    assert github.calls == []
    assert "merging is the seat's grant" in result["notices"][0]["text"]


def test_a_non_number_is_dropped(tmp_path, monkeypatch):
    github = FakeGitHub([("ci", "SUCCESS")])
    result = _drive(tmp_path, monkeypatch, github, FakeCheckout(), text="---\nland: soon\n---\n")
    assert github.calls == []
    assert result["notices"][0]["kind"] == "dropped"


def test_gh_checks_json_parse_is_structural(monkeypatch, tmp_path):
    """The real client parses `gh pr checks --json name,state` even on the
    non-zero exit gh uses for pending (8) and failing (1) checks."""
    import subprocess

    def fake_run(argv, **_kw):
        return subprocess.CompletedProcess(
            argv, 8,
            stdout=json.dumps([{"name": "Backend tests", "state": "pending"}]),
            stderr="",
        )

    monkeypatch.setattr(land.subprocess, "run", fake_run)
    checks = land.GhCli(tmp_path, "o/r").checks(12)
    assert checks == [land.Check("Backend tests", "PENDING")]


def test_gh_merge_is_pinned_to_the_read_head(monkeypatch, tmp_path):
    import subprocess

    seen: list[list[str]] = []

    def fake_run(argv, **_kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(land.subprocess, "run", fake_run)
    land.GhCli(tmp_path, "o/r").merge(12, head_sha=HEAD)
    assert seen == [[
        "gh", "pr", "merge", "12", "--squash", "--match-head-commit", HEAD, "--repo", "o/r",
    ]]


def test_a_checkout_read_failing_after_the_merge_still_produces_and_announces(tmp_path, monkeypatch):
    class Flaky(FakeCheckout):
        def head(self):
            raise land.LandError("`git rev-parse` did not answer")

    result = _drive(tmp_path, monkeypatch, FakeGitHub([("ci", "SUCCESS")]), Flaky())
    assert result["produce"][0]["ref"] == MERGED
    assert "not fast-forwarded — LandError: `git rev-parse` did not answer" in _reply_text(result)


def test_an_unexpected_raise_costs_the_file_not_the_tick(tmp_path, monkeypatch):
    class Broken(FakeGitHub):
        def pr(self, number):
            raise RuntimeError("gh exploded")

    result = _drive(tmp_path, monkeypatch, Broken([("ci", "SUCCESS")]), FakeCheckout())
    (notice,) = result["notices"]
    assert "drain error: RuntimeError: gh exploded processing land.md" in notice["text"]
    assert (notice["source_file"], notice["verb"]) == ("land.md", "land")
    assert (result["outbox"] / ".poisoned").exists()
