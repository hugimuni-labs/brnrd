"""A body's friction: the verb and the molt field it rides on, and the scar whose fix loaded."""

import json
import subprocess

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.selfrepo import init_self
from brr.loom.runtime.wishes import wishes

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies

SCAR = "memory/scars/loom/a-send-loses-its-wish.md"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _land_scar(root, path=SCAR, head="upstream: brnrd#7"):
    """A scar on self main, as a body's send-self would leave it."""
    self_repo = root / "self"
    target = self_repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# A send loses its wish\ntrigger: send --wish\n2026-11-28\n{head}\n\nThe workaround.\n")
    _git(self_repo, "add", path)
    _git(self_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "scar")


def test_friction_verb_and_molt_record_where_it_went_and_make_own_work_only_when_unsorted(tmp_path):
    """The runtime verb and the molt, from a body: refused without its artifact, recorded with it."""
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work\n\n## Now\nNext step: friction", "work", wait="0")
    _land_scar(root)
    inject_letter(root, to="thread:work", body="go")
    out = root / "body.json"
    bodies(root, f'''import json, subprocess, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
if "Carried across:" in ctx.wake():
    atomic_write(Path({str(out)!r} + ".wake"), ctx.wake())
    for letter in letters:
        ctx.send("thread:work", note="seen", re=letter.id)
else:
    def run(*argv):
        proc = subprocess.run([sys.executable, "-m", "brr.loom.runtime", *argv, "--room", str(ctx.room)],
                              capture_output=True, text=True, env=ctx.env, cwd=ctx.room)
        return {{"code": proc.returncode, "out": proc.stdout, "err": proc.stderr}}
    seen = {{
        "missing": run("friction", "a send drops its wish", "--sink", "upstream",
                       "--at", "memory/scars/loom/never-landed.md"),
        "bare": run("friction", "a send drops its wish", "--sink", "scar"),
        "upstream": run("friction", "a send drops its wish", "--sink", "upstream", "--at", {SCAR!r}),
        "unsorted": run("friction", "the boundary repeats a park line hourly"),
        "molt_bad": run("molt", "--why", "done", "--friction", "x", "--sink", "fit", "--at", "nope.md"),
    }}
    for letter in letters:
        ctx.send("thread:work", note="recorded", re=letter.id)
    seen["molt"] = run("molt", "--why", "carry the friction", "--friction", "the send dropped it again",
                       "--sink", "upstream", "--at", {SCAR!r})
    atomic_write(Path({str(out)!r}), json.dumps(seen))
    assert ctx.jack("stop").raw == ""
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: (root / "body.json.wake").exists(), 40, loom.dump)
        seen = json.loads(out.read_text())
        wake = (root / "body.json.wake").read_text()
        facts = loom.facts()
    finally:
        loom.halt()
    # Refused: no artifact on main, no --at, and a molt whose friction cannot be recorded does not go.
    assert seen["missing"]["code"] != 0 and "not on self main" in seen["missing"]["err"], seen["missing"]
    assert seen["bare"]["code"] != 0 and "needs --at" in seen["bare"]["err"], seen["bare"]
    assert seen["molt_bad"]["code"] != 0 and "not on self main" in seen["molt_bad"]["err"], seen["molt_bad"]
    # Positive controls: the same verb with its scar on main records, and so does the molt.
    assert seen["upstream"]["code"] == 0, seen["upstream"]
    assert seen["unsorted"]["code"] == 0 and "own wish" in seen["unsorted"]["out"], seen["unsorted"]
    assert seen["molt"]["code"] == 0, seen["molt"]
    frictions = [f for f in facts if f.kind == "friction"]
    assert [(f.data["sink"], f.data.get("at"), f.data["thread"]) for f in frictions] == [
        ("upstream", SCAR, "work"), ("", None, "work"), ("upstream", SCAR, "work")]
    # This test loom runs no checkout of brnrd: plain, so the upstream frictions make no fix wish.
    assert {f.data["install"] for f in frictions} == {"plain"}
    assert "paid 1" in seen["upstream"]["out"]
    own = [w for w in wishes(facts).values() if w.sender == "self"]
    assert [(w.line, w.thread, w.origin, w.state) for w in own] == [
        ("sort: the boundary repeats a park line hourly", "inbox", "own", "heard")]
    assert frictions[1].data["wish"] == own[0].id
    # The person's queue is not diluted: an own wish is nobody's ask and no person's wish.
    from brr.loom.runtime.queues import queues
    q = queues(wishes(facts), {})
    assert own[0].id not in {w.id for w in [*q.mine, *q.idle, *q.yours]}
    molt = next(f for f in facts if f.kind == "reincarnate")
    assert molt.data["friction"] == {"id": frictions[2].id, "line": "the send dropped it again", "sink": "upstream"}
    assert "Its friction (upstream): the send dropped it again. Carried across:" in wake


def test_an_upstream_friction_on_a_brnrd_checkout_makes_a_fix_wish(tmp_path):
    """Upstream install: a checkout of brnrd following main. A fork is dev; a release is plain."""
    from brr.loom.runtime import friction
    root = tmp_path / "home"
    init_self(root, person="test")
    _land_scar(root)
    source = tmp_path / "src"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "remote", "add", "origin", "https://github.com/hugimuni-labs/brnrd.git")
    home = Home(root)
    assert friction.install_kind(home, source) == "upstream"
    _git(source, "config", "brnrd.update", "stable")
    assert friction.install_kind(home, source) == "plain"
    _git(source, "config", "brnrd.update", "dev")
    _git(source, "remote", "set-url", "origin", "git@github.com:someone/brnrd.git")
    assert friction.install_kind(home, source) == "dev"
    assert friction.install_kind(home, tmp_path / "no-checkout") == "plain"
    _git(source, "remote", "set-url", "origin", "https://github.com/hugimuni-labs/brnrd")
    import brr.loom.runtime.receipts as receipts
    original = receipts.source_root
    receipts.source_root = lambda: source
    try:
        out = friction.facts_for(home, read_facts(home), by="strand:s-x", thread="work", strand="s-x", gen=1,
                                 line="a send drops its wish", sink="upstream", at=SCAR)
        scar_only = friction.facts_for(home, read_facts(home), by="strand:s-x", thread="work", strand="s-x",
                                       gen=1, line="I misread the agreement", sink="scar", at=SCAR)
    finally:
        receipts.source_root = original
    assert [f.kind for f in out] == ["friction", "wish"]
    assert out[1].data["line"] == "fix upstream: a send drops its wish" and out[1].data["from"] == "self"
    assert out[1].data["thread"] == home.main_thread()
    assert [f.kind for f in scar_only] == ["friction"]


def _source(tmp_path):
    """A brnrd checkout with pull request #7 merged on main, and the commit before it."""
    source = tmp_path / "brnrd"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "remote", "add", "origin", "https://github.com/hugimuni-labs/brnrd.git")
    env = ["-c", "user.name=t", "-c", "user.email=t@t"]
    _git(source, *env, "commit", "-q", "--allow-empty", "-m", "base")
    before = _git(source, "rev-parse", "HEAD")
    _git(source, *env, "commit", "-q", "--allow-empty", "-m", "Keep the wish on a send (#7)")
    return source, before, _git(source, "rev-parse", "HEAD")


def _started(home, commit):
    append(home, Fact(kind="loom.started", by="loom:test", data={"install": home.install_id(), "pid": 1,
                                                                         "commit": commit, "loaded": []}))


def _loaded(home, facts):
    return [f for f in facts if f.kind == "letter" and f.data.get("from") == "loom" and "scar-loaded" in f.id]


def test_a_scars_upstream_fix_raises_one_notice_once_loaded_never_on_merge_alone(tmp_path):
    from brr.loom.runtime.friction import loaded_notices, upstream_ref
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work", "work", wait="0")
    _land_scar(root)
    _land_scar(root, "memory/scars/git/no-upstream.md", head="nothing filed")
    home = Home(root)
    append(home, Fact(kind="friction", by="strand:s-x", id="friction:one",
                      data={"line": "x", "sink": "upstream", "at": SCAR, "thread": "work", "strand": "s-x"}))
    source, before, merged = _source(tmp_path)
    # Merged upstream, recorded as merged, but the loom still runs the code before it: nothing.
    append(home, Fact(kind="pr.merged", by="strand:s-x", id="pr.merged:work:brnrd:7",
                      data={"thread": "work", "repo": "brnrd", "number": 7, "head": "loom/work",
                            "merge_commit": merged}))
    _started(home, before)
    assert loaded_notices(home, read_facts(home), root=source) == []
    assert _loaded(home, read_facts(home)) == []
    # Positive control: the same scar once the loom runs the merge.
    _started(home, merged)
    assert loaded_notices(home, read_facts(home), root=source) == [SCAR]
    notice = _loaded(home, read_facts(home))
    assert len(notice) == 1 and notice[0].data["to"] == "thread:work"
    assert f"scar {SCAR}: its fix brnrd#7 loaded at {merged[:8]}" in notice[0].data["body"]
    # Once: the next check, and the next start, write nothing more.
    _started(home, merged)
    assert loaded_notices(home, read_facts(home), root=source) == []
    assert len(_loaded(home, read_facts(home))) == 1
    # The line's forms, read from the head only.
    assert upstream_ref("# t\nupstream: #12\n") == 12
    assert upstream_ref("# t\nUpstream: https://github.com/hugimuni-labs/brnrd/pull/34\n") == 34
    assert upstream_ref("# t\nupstream: otherrepo#5\n") is None
    assert upstream_ref("# t\n" + "line\n" * 12 + "upstream: brnrd#9\n") is None
