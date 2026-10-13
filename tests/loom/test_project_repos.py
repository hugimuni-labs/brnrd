"""A seat's repo: is a checkout beside self. Local bare repos only; no network."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.loom import _env, tick_once
from brr.loom.runtime.repos import prepare_repos
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import _commit, git, init_self

from _step import Loom, kill_bodies, wait_until, write_thread
from test_lifecycle import bodies

LABEL = "\n\nLoom-Label: taint=0; audience=self\n"
AUTHOR = "Ada Lovelace <ada@example.com>"
LOOM = "brnrd-loom <loom@localhost>"


def _source(tmp_path: Path, name: str = "widget") -> tuple[Path, Path]:
    work = tmp_path / "work" / name
    work.mkdir(parents=True)
    git(None, "init", "-b", "main", str(work))
    (work / "README.md").write_text(f"{name}\n")
    git(work, "add", "README.md")
    git(work, "commit", "-m", "start")
    bare = tmp_path / "sources" / f"{name}.git"
    bare.parent.mkdir(parents=True)
    git(None, "clone", "--bare", str(work), str(bare))
    return bare, work


def _author(root: Path, author: str = AUTHOR) -> None:
    path = root / "loom" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'author = "{author}"\n')


def _seat(root: Path, thread: str, sources: list[str], policy: str = "quit-now") -> None:
    write_thread(root, thread, f"# {thread}\n", policy, wait="0")
    lines = "".join(f"repo: {source}\n" for source in sources)
    (root / "self" / "threads" / thread / "seat").write_text(lines)
    git(root / "self", "add", "threads")
    _commit(root / "self", f"Seat {thread}.{LABEL}")
    inject_letter(root, to=f"thread:{thread}", body=f"open {thread}")


def _home(tmp_path: Path, *, author: str | None = AUTHOR) -> Path:
    root = tmp_path / "home"
    init_self(root, person="test")
    if author is not None:
        _author(root, author)
    return root


def _enroll(root: Path, email: str) -> None:
    path = root / "self" / "core" / "loom" / "people-commit"
    path.write_text(path.read_text(encoding="utf-8") + email + "\n", encoding="utf-8")
    git(root / "self", "add", "core/loom/people-commit")
    _commit(root / "self", f"Enroll {email}.{LABEL}")


def _who(repo: Path) -> str:
    return git(repo, "log", "-1", "--format=%an <%ae> %cn <%ce>").stdout.strip()


def _note(repo: Path, room: Path, rel: str, text: str) -> None:
    env = _env(room)
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", rel, env=env)
    git(repo, "commit", "-m", rel, env=env)


def _tick(home: Home, bodies: dict, router: Router) -> None:
    tick_once(home, bodies, "fake", "haiku", router)


def _kill(facts) -> None:
    for fact in facts:
        if fact.kind != "body.started":
            continue
        pid = fact.data.get("pid")
        if not isinstance(pid, int):
            continue
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _room(root: Path) -> Path:
    rooms = [path for path in (root / "rooms").iterdir() if path.is_dir()]
    assert len(rooms) == 1, [path.name for path in rooms]
    return rooms[0]


def _started(root: Path) -> list:
    return [fact for fact in read_facts(Home(root)) if fact.kind == "body.started"]


def _log(root: Path) -> str:
    path = root / "loom" / "loom.log"
    return path.read_text() if path.is_file() else ""


def test_a_seat_repo_is_checked_out_on_the_thread_branch(tmp_path: Path) -> None:
    root = _home(tmp_path)
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    bodies: dict = {}
    try:
        _tick(home, bodies, Router(home))
        assert _started(root), _log(root)
        room = _room(root)
        checkout = room / "widget"
        assert (checkout / ".git").is_dir()
        assert git(checkout, "branch", "--show-current").stdout.strip() == "loom/work"
        assert git(checkout, "rev-parse", "HEAD").stdout.strip() == git(
            checkout, "rev-parse", "origin/main").stdout.strip()
        assert "widget.git/objects" in (checkout / ".git/objects/info/alternates").read_text()
        assert git(checkout, "remote", "get-url", "origin").stdout.strip() == str(bare)
        wake = (room / "port" / "wake.md").read_text()
        loom_at = wake.index("## Loom\n")
        rest = wake[loom_at + 1:]
        end = rest.find("\n## ")
        section = wake[loom_at:] if end < 0 else wake[loom_at:loom_at + 1 + end]
        line = f"repo widget: {checkout} on loom/work, cut from origin/main"
        assert line in section
    finally:
        _kill(read_facts(home))


def test_commits_carry_the_home_author_in_the_checkout_and_in_self(tmp_path: Path) -> None:
    root = _home(tmp_path)
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        assert _started(root), _log(root)
        room = _room(root)
        env = _env(room)
        assert env["GIT_AUTHOR_NAME"] == "Ada Lovelace"
        assert env["GIT_AUTHOR_EMAIL"] == "ada@example.com"
        assert env["GIT_COMMITTER_NAME"] == "Ada Lovelace"
        assert env["GIT_COMMITTER_EMAIL"] == "ada@example.com"
        carried = f"{AUTHOR} {AUTHOR}"
        _note(room / "widget", room, "note.txt", "project\n")
        _note(room / "self", room, "memory/moves/self-note.md", "self\n")
        assert _who(room / "widget") == carried
        assert _who(room / "self") == carried
    finally:
        _kill(read_facts(home))


def test_commits_carry_the_loom_when_author_is_unset(tmp_path: Path) -> None:
    root = _home(tmp_path, author=None)
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        assert _started(root), _log(root)
        room = _room(root)
        env = _env(room)
        assert env["GIT_AUTHOR_NAME"] == "brnrd-loom"
        assert env["GIT_AUTHOR_EMAIL"] == "loom@localhost"
        assert env["GIT_COMMITTER_EMAIL"] == "loom@localhost"
        carried = f"{LOOM} {LOOM}"
        _note(room / "widget", room, "note.txt", "project\n")
        _note(room / "self", room, "memory/moves/self-note.md", "self\n")
        assert _who(room / "widget") == carried
        assert _who(room / "self") == carried
    finally:
        _kill(read_facts(home))


def test_an_author_enrolled_only_in_the_worktree_still_starts(tmp_path: Path) -> None:
    root = _home(tmp_path)
    listing = root / "self" / "core" / "loom" / "people-commit"
    listing.write_text(listing.read_text(encoding="utf-8") + "ada@example.com\n", encoding="utf-8")
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        assert _started(root), _log(root)
        assert "ada@example.com" not in git(
            root / "self", "show", "main:core/loom/people-commit").stdout
    finally:
        _kill(read_facts(home))


def test_an_enrolled_author_starts_no_body(tmp_path: Path) -> None:
    root = _home(tmp_path)
    _enroll(root, "ada@example.com")
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        facts = read_facts(home)
        assert not any(fact.kind == "body.started" for fact in facts)
        notes = [fact for fact in facts if fact.kind == "letter"
                 and "ada@example.com" in fact.data.get("body", "")]
        assert len(notes) == 1
        assert "choose an author that is not an enrolled person" in notes[0].data["body"]
        assert not (root / "rooms").exists() or not any((root / "rooms").iterdir())
    finally:
        _kill(read_facts(home))


def test_a_malformed_author_starts_no_body(tmp_path: Path) -> None:
    root = _home(tmp_path, author="Ada Lovelace")
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        facts = read_facts(home)
        assert not any(fact.kind == "body.started" for fact in facts)
        notes = [fact for fact in facts if fact.kind == "letter"
                 and "Name <email>" in fact.data.get("body", "")]
        assert len(notes) == 1
        assert "loom/config.toml" in notes[0].data["body"]
        assert not (root / "rooms").exists() or not any((root / "rooms").iterdir())
    finally:
        _kill(read_facts(home))


def test_a_second_start_leaves_the_checkout(tmp_path: Path) -> None:
    root = _home(tmp_path)
    bare, work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        assert _started(root), _log(root)
        room = _room(root)
        checkout = room / "widget"
        (checkout / "kept.txt").write_text("kept\n")
        env = _env(room)
        git(checkout, "add", "kept.txt", env=env)
        git(checkout, "commit", "-m", "keep", env=env)
        sha = git(checkout, "rev-parse", "HEAD").stdout.strip()
        (work / "README.md").write_text("moved\n")
        git(work, "add", "README.md")
        git(work, "commit", "-m", "move main")
        pushed = git(work, "push", str(bare), "HEAD:main", check=False)
        assert pushed.returncode == 0, pushed.stderr
        text, failures = prepare_repos(home, room.name, "work", [("widget", str(bare))], None)
        assert failures == ()
        assert f"repo widget: {checkout} on loom/work, left as it stands\n" == text
        assert git(checkout, "rev-parse", "HEAD").stdout.strip() == sha
        assert (checkout / "kept.txt").read_text() == "kept\n"
        assert git(checkout, "branch", "--show-current").stdout.strip() == "loom/work"
        assert git(root / "repos" / "widget.git", "rev-parse", "main").stdout.strip() != sha
    finally:
        _kill(read_facts(home))


def test_molt_successor_holds_the_predecessor_commit(tmp_path: Path) -> None:
    root = _home(tmp_path)
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)], policy="quit-now")
    pred = root / "pred.json"
    received = root / "successor.json"
    bodies(root, f'''import json, subprocess, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
checkout = ctx.room / "widget"
if "cut from parent" in ctx.wake():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    Path({str(received)!r}).write_text(json.dumps({{
        "sha": sha,
        "kept": (checkout / "kept.txt").read_text(),
        "wake": ctx.wake(),
    }}))
    for letter in ctx.owed():
        ctx.send("thread:work", note="carried", re=letter.id)
else:
    if not (checkout / ".git").is_dir():
        raise SystemExit("no checkout\\n" + ctx.wake())
    (checkout / "kept.txt").write_text("kept\\n")
    subprocess.check_call(["git", "add", "kept.txt"], cwd=checkout)
    subprocess.check_call(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "keep"], cwd=checkout)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    Path({str(pred)!r}).write_text(json.dumps({{
        "sha": sha, "cwd": str(Path.cwd()), "room": str(ctx.room),
    }}))
    ctx.molt("carry the checkout")
    assert ctx.jack("stop").raw == ""
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(received.exists, 30, loom.dump)
        first = json.loads(pred.read_text())
        second = json.loads(received.read_text())
        assert Path(first["cwd"]).resolve() == (Path(first["room"]) / "self").resolve()
        assert second["sha"] == first["sha"]
        assert second["kept"] == "kept\n"
        assert "cut from parent" in second["wake"]
        parent = Path(first["room"]) / "widget"
        assert git(parent, "rev-parse", "HEAD").stdout.strip() == first["sha"]
        assert (parent / "kept.txt").read_text() == "kept\n"
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors, loom.dump()


def test_an_unreachable_source_starts_and_says_so(tmp_path: Path) -> None:
    root = _home(tmp_path)
    missing = tmp_path / "nowhere" / "missing.git"
    _seat(root, "work", [str(missing)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        assert _started(root), _log(root)
        room = _room(root)
        wake = (room / "port" / "wake.md").read_text()
        assert "repo missing: fetch failed; no checkout" in wake
        assert not (room / "missing" / ".git").exists()
    finally:
        _kill(read_facts(home))


def test_a_failed_fetch_keeps_the_last_state(tmp_path: Path) -> None:
    root = _home(tmp_path)
    bare, _work = _source(tmp_path)
    _seat(root, "held", [str(bare)], policy="hold")
    home = Home(root)
    bodies: dict = {}
    router = Router(home)
    try:
        _tick(home, bodies, router)
        assert _started(root), _log(root)
        room = _room(root)
        checkout = room / "widget"
        sha = git(checkout, "rev-parse", "HEAD").stdout.strip()
        mirror = root / "repos" / "widget.git"
        short = git(mirror, "rev-parse", "--short", "HEAD").stdout.strip()
        # The source stays the mirror's origin and stops resolving, so this is
        # a failed fetch of the same source. A different URL is the other test.
        bare.rename(bare.parent / "widget-gone.git")
        text, failures = prepare_repos(home, room.name, "held", [("widget", str(bare))], None)
        failed = f"repo widget: {checkout} on loom/held, left as it stands; fetch failed"
        assert text == failed + "\n"
        assert failures == (failed,)
        assert git(checkout, "rev-parse", "HEAD").stdout.strip() == sha
        _seat(root, "next", [str(bare)], policy="quit-now")
        _tick(home, bodies, router)
        rooms = {path.name: path for path in (root / "rooms").iterdir() if path.is_dir()}
        assert room.name in rooms and len(rooms) == 2
        fresh = next(path for name, path in rooms.items() if name != room.name)
        wake = (fresh / "port" / "wake.md").read_text()
        assert (
            f"repo widget: {fresh / 'widget'} on loom/next, cut from origin/main; "
            f"fetch failed, on the last fetched state {short}"
        ) in wake
        assert (fresh / "widget" / ".git").is_dir()
        assert git(fresh / "widget", "remote", "get-url", "origin").stdout.strip() == str(bare)
        assert git(fresh / "widget", "rev-parse", "HEAD").stdout.strip() == sha
    finally:
        _kill(read_facts(home))


def test_duplicate_repo_names_are_refused(tmp_path: Path) -> None:
    root = _home(tmp_path)
    one, _work = _source(tmp_path, "widget")
    two = tmp_path / "other" / "widget.git"
    _seat(root, "work", [str(one), str(two)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        facts = read_facts(home)
        assert not any(fact.kind == "body.started" for fact in facts)
        notes = [fact for fact in facts if fact.kind == "letter"
                 and "declared twice" in fact.data.get("body", "")]
        assert len(notes) == 1
        assert "correct the repo lines" in notes[0].data["body"]
        assert not (root / "rooms").exists() or not any((root / "rooms").iterdir())
    finally:
        _kill(read_facts(home))


def test_two_sources_with_one_name_do_not_share_a_mirror(tmp_path: Path) -> None:
    root = _home(tmp_path)
    one, _work = _source(tmp_path, "widget")
    other = tmp_path / "other-work"
    other.mkdir()
    git(None, "init", "-b", "main", str(other))
    (other / "README.md").write_text("other\n")
    git(other, "add", "README.md")
    git(other, "commit", "-m", "other")
    two = tmp_path / "elsewhere" / "widget.git"
    two.parent.mkdir()
    git(None, "clone", "--bare", str(other), str(two))
    _seat(root, "one", [str(one)])
    _seat(root, "two", [str(two)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        assert len(_started(root)) == 2, _log(root)
        rooms = [path for path in (root / "rooms").iterdir() if path.is_dir()]
        assert len(rooms) == 2, [path.name for path in rooms]
        held = [path for path in rooms if "the mirror holds" in (path / "port" / "wake.md").read_text()]
        opened = [path for path in rooms if (path / "widget" / ".git").is_dir()]
        assert len(held) == 1 and len(opened) == 1, _log(root)
        assert held[0] != opened[0]
        mirror = git(root / "repos" / "widget.git", "remote", "get-url", "origin").stdout.strip()
        wake = (held[0] / "port" / "wake.md").read_text()
        line = f"repo widget: the mirror holds {mirror}; no checkout"
        assert line in wake
        assert not (held[0] / "widget" / ".git").exists()
        assert git(opened[0] / "widget", "remote", "get-url", "origin").stdout.strip() == mirror
        assert mirror in {str(one), str(two)}
        assert line in _log(root)
    finally:
        _kill(read_facts(home))


def test_a_checkout_push_has_no_credential(tmp_path: Path) -> None:
    root = _home(tmp_path)
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    try:
        _tick(home, {}, Router(home))
        assert _started(root), _log(root)
        room = _room(root)
        checkout = room / "widget"
        git(checkout, "remote", "set-url", "origin", "https://example.com/widget.git")
        assert git(checkout, "remote", "get-url", "origin").stdout.strip() == (
            "https://example.com/widget.git")
        for repo in (checkout, room / "self"):
            proc = git(repo, "config", "--local", "--get", "credential.helper", check=False)
            assert proc.returncode == 0, repo
            assert proc.stdout.strip() == ""
    finally:
        _kill(read_facts(home))
