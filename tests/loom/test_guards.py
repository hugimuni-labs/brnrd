"""Guards on the self's main: warn at the tool boundary, refuse the outward acts."""

import json
import subprocess
from pathlib import Path

import pytest

from brr.loom.runtime.guards import GuardError, glob_regex, parse
from brr.loom.runtime.jack import execute
from brr.loom.runtime.merge import SendError, send_to_self

STRAND = "s-ab12-aaaaaa"

MIGRATIONS = """
says = "Leave migrations/ alone: Arseni asked (4726)."
set-by = "letter:4726"
scope = "repo:proj"

[[paths]]
untouched = ["migrations/"]
absent = ["probe.config.ts"]
"""

STASH = """
says = "Worktrees share one stash list: pop by name."
set-by = "scar:git.md#stash-pop"

[[match]]
tool = "Bash"
input = '''\\bgit\\s+stash\\s+pop\\b'''
unless = '''stash@\\{\\d+\\}'''

[[match]]
at = "post"
tool = "Bash"
output = '''error code: 1010'''
"""


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                           *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _repo(path: Path, files: dict) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", "seed")
    return path


def _home(tmp_path, guards: dict) -> Path:
    """A home whose self's main carries ``guards``, and a room with a ``proj`` checkout."""
    _repo(tmp_path / "self", {f"core/guards/{k}.toml": v for k, v in guards.items()} | {"README.md": "self\n"})
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    upstream = _repo(tmp_path / "upstream", {"migrations/001.sql": "create\n", "src/a.py": "a\n"})
    git(tmp_path, "clone", "-q", str(upstream), str(room / "proj"))
    git(room / "proj", "switch", "-qc", "loom/ta")
    return room


def _call(tool, **tool_input):
    return json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": "/tmp"})


def _context(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_a_matching_call_hears_the_guard_before_it_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room = _home(tmp_path, {"stash-pop": STASH})
    out = execute("pre", room, _call("Bash", command="cd x && git stash pop"))
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert _context(out) == "guard stash-pop (scar:git.md#stash-pop): Worktrees share one stash list: pop by name."
    assert execute("pre", room, _call("Bash", command="git stash pop stash@{2}")) == ""
    assert execute("pre", room, _call("Bash", command="git status")) == ""
    # A pre call is no boundary: it leaves the jack's state alone.
    assert not (room / "port" / "jack-state.json").exists()


def test_a_post_match_reads_the_output_and_rides_with_the_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room = _home(tmp_path, {"stash-pop": STASH})
    payload = json.loads(_call("Bash", command="curl x"))
    payload["tool_response"] = {"stdout": "error code: 1010", "stderr": ""}
    assert "Worktrees share" in _context(execute("post", room, json.dumps(payload)))
    payload["tool_response"] = {"stdout": "ok", "stderr": ""}
    assert execute("post", room, json.dumps(payload)) == ""


def test_an_edit_into_an_untouched_path_warns_and_the_stop_blocks_once(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room = _home(tmp_path, {"migrations": MIGRATIONS})
    target = room / "proj" / "migrations" / "002.sql"
    said = _context(execute("pre", room, _call("Write", file_path=str(target), content="drop\n")))
    assert "Leave migrations/ alone" in said and "proj: migrations/002.sql is guarded" in said
    assert execute("pre", room, _call("Write", file_path=str(room / "proj/src/b.py"), content="")) == ""

    target.write_text("drop\n")  # untracked counts
    first = json.loads(execute("stop", room, "{}"))
    assert first["decision"] == "block"
    assert "proj: migrations/002.sql changed" in first["reason"]
    assert "letter:4726" in first["reason"]


def test_the_same_breaks_let_the_second_stop_through(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "91")  # ceiling 1s: the hold returns at once
    room = _home(tmp_path, {"migrations": MIGRATIONS})
    (room / "proj" / "migrations" / "001.sql").write_text("changed\n")
    assert json.loads(execute("stop", room, "{}"))["decision"] == "block"
    second = execute("stop", room, "{}")
    assert "path guard" not in second
    (room / "proj" / "probe.config.ts").write_text("x")
    third = json.loads(execute("stop", room, "{}"))
    assert "proj: probe.config.ts exists" in third["reason"]


def test_a_guard_scoped_to_another_repo_or_thread_stays_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    other = MIGRATIONS.replace('scope = "repo:proj"', 'scope = "thread:tb"\n') \
        .replace("[[paths]]", '[[paths]]\nrepo = "proj"')
    room = _home(tmp_path, {"migrations": other})
    target = room / "proj" / "migrations" / "001.sql"
    assert execute("pre", room, _call("Edit", file_path=str(target), new_string="x")) == ""


def test_a_broken_guard_file_is_skipped_whole_and_named_at_start(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room = _home(tmp_path, {"stash-pop": STASH, "bad": 'says = "x"\nset-by = "letter:1"\n[[match]]\ninput = "("\n'})
    (room / "port" / "in" / "boundary.md").write_text("")
    out = execute("start", room, "")
    assert "guards: skipped bad: match[0].input is not a regex" in out
    assert "Worktrees share" in _context(execute("pre", room, _call("Bash", command="git stash pop")))


def test_a_guard_on_a_branch_is_not_a_guard_until_it_is_on_main(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room = _home(tmp_path, {})
    git(tmp_path / "self", "switch", "-qc", "side")
    (tmp_path / "self/core/guards").mkdir(parents=True)
    (tmp_path / "self/core/guards/stash-pop.toml").write_text(STASH)
    git(tmp_path / "self", "add", "-A")
    git(tmp_path / "self", "commit", "-qm", "side")
    assert execute("pre", room, _call("Bash", command="git stash pop")) == ""
    git(tmp_path / "self", "switch", "-q", "main")
    git(tmp_path / "self", "merge", "-q", "side")
    assert execute("pre", room, _call("Bash", command="git stash pop")) != ""


def test_send_self_refuses_a_landing_that_breaks_a_self_path_guard(tmp_path, monkeypatch):
    guard = 'says = "core/identity.md is his."\nset-by = "letter:9"\n[[paths]]\nuntouched = ["core/identity.md"]\n'
    room = _home(tmp_path, {"identity": guard})
    git(tmp_path / "self", "config", "receive.denyCurrentBranch", "updateInstead")
    git(tmp_path, "clone", "-q", str(tmp_path / "self"), str(room / "self"))
    clone = room / "self"
    git(clone, "switch", "-qc", f"strand/{STRAND}")
    (clone / "core").mkdir(exist_ok=True)
    (clone / "core/identity.md").write_text("mine now\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "edit identity")
    monkeypatch.setattr("brr.loom.runtime.lifecycle.own_thread", lambda room: "ta")
    with pytest.raises(SendError, match="self: core/identity.md changed"):
        send_to_self(clone)
    assert "mine now" not in git(tmp_path / "self", "log", "-p", "main")


@pytest.mark.parametrize("glob,yes,no", [
    ("migrations/", ["migrations/a.sql", "migrations/x/b.sql"], ["migrations.txt", "src/migrations/a"]),
    ("**/migrations/**", ["migrations/a", "src/migrations/a"], ["src/migration/a"]),
    ("src/*.py", ["src/a.py"], ["src/x/a.py"]),
])
def test_globs(glob, yes, no):
    pattern = glob_regex(glob)
    assert all(pattern.match(p) for p in yes)
    assert not any(pattern.match(p) for p in no)


@pytest.mark.parametrize("text,error", [
    ('set-by = "letter:1"\n[[match]]\ninput = "x"\n', "says is required"),
    ('says = "x"\nset-by = "me"\n[[match]]\ninput = "x"\n', "set-by is required"),
    ('says = "x"\nset-by = "letter:1"\n', "needs [[paths]] or [[match]]"),
    ('says = "x"\nset-by = "letter:1"\n[[match]]\noutput = "x"\n', 'needs at = "post"'),
    ('says = "x"\nset-by = "letter:1"\nscope = "everyone"\n[[match]]\ninput = "x"\n', "scope is"),
    ('says = "x"\nset-by = "letter:1"\nwhen = 1\n[[match]]\ninput = "x"\n', "unknown keys when"),
])
def test_parse_names_the_field(text, error):
    with pytest.raises(GuardError, match=error.replace("[", r"\[").replace("]", r"\]")):
        parse("g", text)
