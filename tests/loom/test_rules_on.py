"""``on:`` rules: a note the self left itself, shown by the jack at the act.

The jack is driven the way test_jack drives it — ``run("post", room, stdin)``
with a hook payload — over a room whose ``self`` is a copy of the seed. The
gate and the wake run as the real scripts.
"""

from __future__ import annotations

import json
import runpy
import shutil
from pathlib import Path

import pytest

from brr.loom.runtime.jack import run as jack
from tests.loom.test_rules_and_measure import SEED, a_self, commit, git, immune, rule, run, wake, write

STRAND = "s-ab12-aaaaaa"
NOTE = "A note you left yourself (memory/rules/{ident}.md, on: {item}):\n"


def a_room(tmp: Path) -> Path:
    room = tmp / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    shutil.copytree(SEED, room / "self")
    return room


def on_rule(room: Path, ident: str, on: str, text: str, extra: str = "") -> None:
    write(room / "self", f"memory/rules/{ident}.md", f"---\nid: {ident}\non: {on}\n{extra}---\n{text}\n")


def call(tool: str, cwd: str = "/work/repo", **tool_input: str) -> str:
    return json.dumps({"hook_event_name": "PostToolUse", "tool_name": tool,
                       "tool_input": tool_input, "cwd": cwd})


def context(room: Path, stdin: str) -> str:
    code, out = jack("post", room, stdin)
    assert code == 0
    if not out:
        return ""
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


# --- the jack ----------------------------------------------------------------


def test_matching_edit_shows_the_note_once_per_body(tmp_path: Path) -> None:
    room = a_room(tmp_path)
    on_rule(room, "migrations", '["Edit migrations/**", "Write migrations/**"]', "Grab the keys: migrations are append-only.")
    first = context(room, call("Edit", file_path="/work/repo/migrations/0042_add.sql"))
    assert first == NOTE.format(ident="migrations", item="Edit migrations/**") + "Grab the keys: migrations are append-only.\n"
    # Once per body life: the second matching call is quiet.
    assert context(room, call("Write", file_path="/work/repo/migrations/0043.sql")) == ""
    assert json.loads((room / "port/jack-state.json").read_text())["rules_shown"] == ["migrations"]
    # A new body starts with no jack-state.json (loom.start unlinks it): it sees the note again.
    (room / "port/jack-state.json").unlink()
    again = context(room, call("Write", file_path="migrations/0044.sql"))
    assert again.startswith(NOTE.format(ident="migrations", item="Write migrations/**"))
    assert not (room / "port/jack-errors.log").exists()


def test_non_matching_call_adds_nothing(tmp_path: Path) -> None:
    room = a_room(tmp_path)
    on_rule(room, "migrations", '["Edit migrations/**"]', "Append-only.")
    assert context(room, call("Edit", file_path="/work/repo/src/app.py")) == ""
    # The tool must match too: a Read of the same path is not an Edit.
    assert context(room, call("Read", file_path="/work/repo/migrations/0042.sql")) == ""
    # A path outside the call's cwd is matched as given, never made relative with ../.
    assert context(room, call("Edit", cwd="/work/repo/src", file_path="/work/repo/migrations/x.sql")) == ""
    assert "rules_shown" not in json.loads((room / "port/jack-state.json").read_text())


def test_bash_command_glob(tmp_path: Path) -> None:
    room = a_room(tmp_path)
    on_rule(room, "push", '["Bash git push*"]', "Push over https with the pinned helper, never ssh.")
    assert context(room, call("Bash", command="git status")) == ""
    out = context(room, call("Bash", command="git push origin brr/x"))
    assert out == NOTE.format(ident="push", item="Bash git push*") + "Push over https with the pinned helper, never ssh.\n"


def test_star_tool_matches_any_tool_and_grep_path(tmp_path: Path) -> None:
    room = a_room(tmp_path)
    on_rule(room, "secrets", '["* *secrets*"]', "Secrets never leave the machine.")
    out = context(room, call("Grep", pattern="token", path="/work/repo/config/secrets"))
    assert out.startswith(NOTE.format(ident="secrets", item="* *secrets*"))


def test_notes_ride_with_the_boundary_text(tmp_path: Path) -> None:
    from tests.loom.test_jack import _letter, _write
    room = a_room(tmp_path)
    on_rule(room, "push", '["Bash git push*"]', "Https only.")
    text = _write(room, [_letter()])
    out = context(room, call("Bash", command="git push"))
    assert out.startswith(text)
    assert out.endswith(NOTE.format(ident="push", item="Bash git push*") + "Https only.\n")


def test_malformed_rule_fails_open_and_does_not_taint(tmp_path: Path) -> None:
    room = a_room(tmp_path)
    on_rule(room, "broken", '["Edit"]', "No glob.")
    write(room / "self", "memory/rules/half.md", "---\nid: half\non: [\"Edit *\"\n")
    on_rule(room, "good", '["Edit *.sql"]', "Sql is reviewed twice.")
    out = context(room, call("Edit", file_path="/work/repo/a.sql"))
    assert out == NOTE.format(ident="good", item="Edit *.sql") + "Sql is reviewed twice.\n"
    log = (room / "port/rule-errors.log").read_text()
    assert "memory/rules/broken.md: skipped: on item is not <Tool> <glob>: Edit" in log
    assert "memory/rules/half.md: skipped:" in log
    # jack-errors.log taints the strand (labels.jack_log_text); a draft rule must not.
    assert not (room / "port/jack-errors.log").exists()


def test_when_only_and_wake_rules_are_not_shown_at_the_act(tmp_path: Path) -> None:
    room = a_room(tmp_path)
    write(room / "self", "memory/rules/voice.md", "---\nid: voice\n---\nDry.\n")
    write(room / "self", "memory/rules/squash.md", "---\nid: squash\nwhen: [merge]\n---\nSquash.\n")
    assert context(room, call("Bash", command="git merge x")) == ""


# --- the gate ----------------------------------------------------------------


@pytest.mark.parametrize("on, error", [
    ('["Edit migrations/**", "Bash git push*"]', None),
    ('["* *.sql"]', None),
    ("[]", "on is empty; drop it for a wake rule"),
    ('"Edit migrations/**"', "on must be a list"),
    ('["Edit"]', "on item is not <Tool> <glob>: Edit"),
    ('[" Edit  "]', "on item is not <Tool> <glob>"),
])
def test_gate_on_valid_and_invalid(tmp_path: Path, on: str, error: str | None) -> None:
    root = a_self(tmp_path)
    old = git(root, "rev-parse", "HEAD")
    rule(root, "r", "Text.", extra=f"on: {on}\n")
    new = commit(root)
    out = immune(root, old, new, "30-rules")
    if error is None:
        assert out.returncode == 0, out.stderr
    else:
        assert out.returncode == 1 and error in out.stderr, out.stderr


# --- the wake ----------------------------------------------------------------


def test_on_only_rule_is_not_a_wake_rule(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    rule(root, "push", "Https only.", extra='on: ["Bash git push*"]\n')
    rule(root, "both", "Both ways.", when="[deploy]", extra='on: ["Bash *deploy*"]\n')
    commit(root)
    out = wake(root, [{"id": "l", "from": "p", "body": "deploy it"}])
    assert out.returncode == 0, out.stderr
    assert "### push" not in out.stdout
    assert "memory/rules/push.md · Https only." in out.stdout
    # A rule with when and on is still a trigger at wake.
    assert "### both · when: deploy\nBoth ways.\n" in out.stdout
    quiet = wake(root)
    assert "## Rules" not in quiet.stdout


# --- parity: the gate and the wake read rules the same way --------------------

PARITY = [
    "---\nid: r\n---\nWake.\n",
    "---\nid: r\nwhen: [deploy]\n---\nT.\n",
    '---\nid: r\non: ["Edit a/*"]\n---\nT.\n',
    '---\nid: r\nwhen: [x]\non: ["Bash git push*", "* *"]\n---\nT.\n',
    "---\nid: r\non: []\n---\nT.\n",
    '---\nid: r\non: ["Edit"]\n---\nT.\n',
    '---\nid: r\non: "Edit a/*"\n---\nT.\n',
    '---\nid: r\non: [" ", "Edit a"]\n---\nT.\n',
    "---\nid: r\nwhen: []\n---\nT.\n",
    "---\nid: r\ncheck: x\n---\nT.\n",
    "---\nid: r\nkeep: wake\n---\nT.\n",
    "---\nid: other\n---\nT.\n",
]


@pytest.mark.parametrize("text", PARITY)
def test_gate_and_wake_agree(tmp_path: Path, text: str) -> None:
    root = tmp_path / "self"
    write(root, "memory/rules/r.md", text)
    gate = run([str(SEED / "core/immune.d/30-rules"), "--file", str(root / "memory/rules/r.md")], tmp_path)
    recipe = runpy.run_path(str(SEED / "core/loom/wake"), run_name="wake")
    try:
        recipe["load_rules"](root)
        wake_ok = True
    except SystemExit:
        wake_ok = False
    assert (gate.returncode == 0) == wake_ok, gate.stderr
