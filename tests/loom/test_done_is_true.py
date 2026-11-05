"""Done is true: a delivery reaches the person only when the forge bears its result out
(verify.py), and a PR that weakens its tests beside a code change says so, as a fact."""
from io import StringIO

import pytest

from brr.loom.runtime import jack, verify
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.selfrepo import git, run_wake
from brr.loom.runtime.verbs_wishes import _wish
from brr.loom.runtime.wishes import wishes
from test_wishes import heard, ledger  # noqa: F401 - the fixture

REPO = "acme/app"
MERGE = "c0ffee00" + "0" * 32
HEAD = "beefcafe" + "0" * 32

WEAK = f"""diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
-def total(xs): return sum(xs)
+def total(xs): return sum(xs) + 1
diff --git a/tests/test_app.py b/tests/test_app.py
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1,9 +1,8 @@
 def test_total():
-    assert total([1, 2]) == 3
+    assert total([1, 2]) >= 3
+@pytest.mark.skip(reason="flaky")
 def test_empty():
-    assert total([]) == 0
-    assert isinstance(total([]), int)
+    pass
"""


class FakeForge:
    """The forge's side of a delivery: one pull request, its diff, its checks."""

    def __init__(self, *, merged=True, passed=None, diff="", verdict="green", body="Fixes it.\n\n<!-- brnrd:aaaa -->"):
        self.merged, self.count, self.text, self.verdict = merged, passed, diff, verdict
        self.body, self.edits = body, []

    def pull(self, repo, number):
        assert (repo, number) == (REPO, 7)
        state = {"merged_at": "2026-11-05T16:00:00Z", "merge_commit_sha": MERGE, "state": "closed"} \
            if self.merged else {"merged_at": None, "state": "open"}
        return dict(state, head={"sha": HEAD}, body=self.body)

    def diff(self, repo, number):
        return self.text

    def passed(self, repo, sha):
        assert sha == HEAD
        return self.count

    def ci(self, repo, sha):
        return self.verdict

    def run(self, repo, ident):
        return {"head_sha": HEAD}

    def set_body(self, repo, number, text):
        self.edits.append(text)
        self.body = text


@pytest.fixture
def seat(ledger, monkeypatch):
    """A person's wish said on a chat, and that chat's outbox."""
    import brr.loom.runtime.port as port
    import brr.loom.runtime.project as project
    home, seat = ledger
    ident = heard(home, seat, "Ship the total")
    letter = wishes(read_facts(home))[ident].says[0].letter
    sent = []
    monkeypatch.setattr(project, "reply_channels", lambda facts, home: {letter: "channel:mine/42"})
    monkeypatch.setattr(port, "write_send", lambda room, **kw: sent.append(kw))
    return home, seat, ident, sent


def deliver(seat, ident, forge, monkeypatch, ref=f"pr:https://github.com/{REPO}/pull/7", line="The total"):
    monkeypatch.setattr(verify, "forge_for", lambda home: forge)
    return _wish([ident, "--room", str(seat), "--result", ref, "--line", line, "--deliver"])


def wake(seat):
    out = StringIO()
    assert run_wake(seat, "work", stdout=out) == 0
    return out.getvalue()


def test_an_open_pull_request_is_held_and_its_fact_is_the_left(seat, monkeypatch, capsys):
    home, room, ident, sent = seat
    assert deliver(room, ident, FakeForge(merged=False), monkeypatch) == 1
    said = capsys.readouterr().err
    assert f"held, not delivered: {REPO}#7 is open, not merged: park on pr:{REPO}#7" in said
    wish = wishes(read_facts(home))[ident]
    assert wish.state != "delivered" and not wish.results[-1]["delivered"]
    assert wish.left["steps"][0].startswith(f"{REPO}#7 is open, not merged")
    assert sent == []
    # The jack lets this stop through on the left; the next wake shows it.
    assert jack._state(room).get("declared") == "left"
    assert f"{REPO}#7 is open, not merged" in wake(room)


def test_a_merged_pull_request_is_delivered_with_the_forges_sha(seat, monkeypatch):
    home, room, ident, sent = seat
    forge = FakeForge(passed=4012)
    assert deliver(room, ident, forge, monkeypatch, line="The total, 4012 passed") == 0
    wish = wishes(read_facts(home))[ident]
    assert wish.state == "delivered"
    assert wish.results[-1]["verified"] == f"{REPO}#7 merged at c0ffee00, 4012 passed as CI says"
    assert f"{REPO}#7 merged at c0ffee00" in sent[0]["body"]
    assert forge.edits == []  # nothing weakened, the body is left alone


def test_a_count_ci_disagrees_with_is_held(seat, monkeypatch, capsys):
    home, room, ident, sent = seat
    assert deliver(room, ident, FakeForge(passed=4009), monkeypatch, line="The total, 4,012 passed") == 1
    assert f"the line says 4012 passed; CI on {REPO}#7 at beefcafe reports 4009 passed" in capsys.readouterr().err
    assert wishes(read_facts(home))[ident].state != "delivered" and sent == []


def test_weakened_tests_go_on_the_pull_request_and_in_the_letter_never_refused(seat, monkeypatch):
    home, room, ident, sent = seat
    forge = FakeForge(diff=WEAK)
    assert deliver(room, ident, forge, monkeypatch) == 0
    line = wishes(read_facts(home))[ident].results[-1]["weakened"]
    assert line.startswith("Tests weakened beside code (a fact for the reviewer): ")
    assert "tests/test_app.py loosens `assert total([1, 2]) == 3` to `assert total([1, 2]) >= 3`" in line
    assert ", skips with `@pytest.mark.skip(reason=\"flaky\")`, drops `assert total([]) == 0`, " in line
    assert line.endswith(", drops `assert isinstance(total([]), int)`")
    assert line in sent[0]["body"]
    # On the PR, above the marker; a second pass replaces it rather than stacking.
    assert forge.body == f"Fixes it.\n\n{line}\n\n<!-- brnrd:aaaa -->"
    verify.mark(home, REPO, 7, forge)
    assert forge.body.count(verify.WEAKENED) == 1


def test_an_unreadable_forge_or_a_ref_off_github_holds(seat, monkeypatch, capsys):
    home, room, ident, sent = seat

    class Down(FakeForge):
        def pull(self, repo, number):
            raise OSError("no route to host")

    assert deliver(room, ident, Down(), monkeypatch) == 1
    assert "the forge could not be read for acme/app#7 (no route to host)" in capsys.readouterr().err
    assert deliver(room, ident, FakeForge(), monkeypatch, ref="pr:https://example.com/1") == 1
    assert "names no pull request on GitHub" in capsys.readouterr().err
    assert sent == []


def test_ci_must_be_green_at_its_commit(seat, monkeypatch, capsys):
    home, room, ident, sent = seat
    run = f"ci:https://github.com/{REPO}/actions/runs/99"
    assert deliver(room, ident, FakeForge(verdict="pending"), monkeypatch, ref=run) == 1
    assert f"CI on {REPO}@beefcafe is pending, not green" in capsys.readouterr().err
    assert deliver(room, ident, FakeForge(), monkeypatch, ref=run) == 0
    assert wishes(read_facts(home))[ident].results[-1]["verified"] == f"CI green on {REPO}@beefcafe"


def test_a_file_must_be_on_the_selfs_main(seat, monkeypatch, capsys):
    home, room, ident, sent = seat
    main = git(home.root / "self", "rev-parse", "main").stdout.strip()
    assert deliver(room, ident, None, monkeypatch, ref="file:threads/work/report.md") == 1
    assert f"threads/work/report.md is not on the self's main at {main[:8]}" in capsys.readouterr().err
    assert deliver(room, ident, None, monkeypatch, ref="file:threads/work/README.md") == 0
    assert wishes(read_facts(home))[ident].results[-1]["verified"] == \
        f"threads/work/README.md is on the self's main at {main[:8]}"


def test_a_diff_of_only_tests_or_only_code_says_nothing():
    code, tests = WEAK.split("diff --git a/tests")
    assert verify.weakened(code) == ""
    assert verify.weakened("diff --git a/tests" + tests) == ""
    moved = WEAK.replace("+    pass\n", "+    assert total([]) == 0\n+    assert isinstance(total([]), int)\n")
    assert "drops" not in verify.weakened(moved)


def test_a_rerun_shard_is_counted_once(monkeypatch):
    """Forge.passed sums the newest check run of each name: a shard's rerun
    leaves its first run at the sha, and that one does not count again."""
    runs = [{"id": 1, "name": "Backend shard 1/2"}, {"id": 2, "name": "Backend shard 2/2"},
            {"id": 3, "name": "Backend shard 2/2"}]
    notes = {1: "1000 passed", 2: "990 passed", 3: "1001 passed"}

    class Checks:
        def _runs(self, repo, sha):
            return runs

    forge = verify.Forge(Checks())
    monkeypatch.setattr(forge, "_call", lambda path: [
        {"title": "tests", "message": notes[int(path.split("/")[-2])]}])
    assert forge.passed(REPO, HEAD) == 2001


def test_a_result_the_loom_cannot_read_is_delivered_labelled(seat, monkeypatch):
    """A letter or a commit names nothing the loom reads: delivered, never held,
    with the label in the result fact and in the person's letter."""
    home, room, ident, sent = seat
    assert deliver(room, ident, FakeForge(), monkeypatch, ref="letter:s-x/abcde") == 0
    result = wishes(read_facts(home))[ident].results[-1]
    assert result["delivered"] and result["word"] == f"letter:s-x/abcde {verify.WORD}"
    assert "verified" not in result
    assert verify.WORD in sent[0]["body"]


def test_a_link_is_fetched_and_still_labelled(seat, monkeypatch):
    home, room, ident, sent = seat
    assert deliver(room, ident, FakeForge(), monkeypatch, ref="url:https://example.com/doc") == 0
    assert wishes(read_facts(home))[ident].results[-1]["word"] == \
        f"https://example.com/doc answered HTTP 200: what it holds is {verify.WORD}"

    def gone(url):
        raise verify.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(verify, "reach", gone)
    assert deliver(room, ident, FakeForge(), monkeypatch, ref="url:https://example.com/gone") == 0
    result = wishes(read_facts(home))[ident].results[-1]
    assert result["delivered"] and result["word"] == f"https://example.com/gone did not answer (HTTP 404): {verify.WORD}"


def test_a_host_file_is_checked_by_its_recorded_sha256(seat, monkeypatch, tmp_path):
    home, room, ident, sent = seat
    made = tmp_path / "cut.mp4"
    made.write_bytes(b"frames")
    assert deliver(room, ident, FakeForge(), monkeypatch, ref=f"host:{made}") == 0
    result = wishes(read_facts(home))[ident].results[-1]
    assert result["verified"] == f"host:{made} stored, sha256 {result['sha256'][:12]} as taken"
    (home.root / "blobs" / result["sha256"]).write_bytes(b"other")
    assert verify.check(home, [], "host", str(made), "x", sha256=result["sha256"]).ok is False
    assert verify.check(home, [], "host", str(made), "x").word
